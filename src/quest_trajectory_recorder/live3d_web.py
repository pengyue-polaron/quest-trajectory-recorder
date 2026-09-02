"""HTTP UI for live Quest trajectory calibration."""

from __future__ import annotations

import json
import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .calibration_profiles import calibration_complete, calibration_file, sanitize_profile
from .live_state import LiveState


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
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 12px; align-items: start; }
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
    .tool-card { display: grid; gap: 9px; }
    .metric-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .metric { border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #fff; }
    .metric .name { color: var(--muted); font-size: 11px; font-weight: 600; margin-bottom: 4px; }
    .metric .state { font-size: 15px; font-weight: 650; line-height: 1.2; }
    .next-action { border-left: 3px solid #111; padding: 8px 10px; background: #fff; font-weight: 600; line-height: 1.35; }
    .command-box { margin: 0; border: 1px solid var(--line); border-radius: 6px; padding: 9px; background: #fff; color: var(--ink); font: 11px/1.45 "SF Mono", Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
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
    .profile-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: center; }
    .profile-row.wide { grid-template-columns: 1fr; }
    input, select {
      width: 100%;
      border: 1px solid var(--line-strong);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 13px;
    }
    input:focus, select:focus { outline: 2px solid #111; outline-offset: 1px; }
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
    .profile-hint { color: var(--muted); font-size: 12px; line-height: 1.45; }
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
        <h1>Quest calibration console</h1>
        <p class="subtitle">A setup tool for controller tracking, saved calibration profiles, and LIBERO teleop settings.</p>
      </div>
      <div class="status">
        <span class="pill"><span id="dot" class="dot"></span><span id="gateText">connecting</span></span>
        <div class="small" id="statusText">Waiting for server events...</div>
      </div>
    </header>

    <div class="layout">
      <section class="stage">
        <div class="stage-head">
          <div class="stage-title" id="stageTitle">Trajectory view</div>
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
        <p class="section-title">Setup</p>
        <div class="card subtle tool-card">
          <div class="metric-row">
            <div class="metric"><div class="name">Quest stream</div><div class="state" id="streamMetric">--</div></div>
            <div class="metric"><div class="name">Profile state</div><div class="state" id="profileMetric">Not loaded</div></div>
          </div>
          <div class="profile-row">
            <select id="profileSelect" aria-label="Saved calibration profiles"></select>
            <input id="profileName" value="libero_default" spellcheck="false" aria-label="Calibration profile name" />
          </div>
          <div class="profile-row" style="margin-top:8px;">
            <button id="loadProfile">Load profile</button>
            <button id="saveProfile" class="primary" disabled>Save profile</button>
          </div>
          <div id="profileStatus" class="profile-hint" style="margin-top:8px;">Loading profiles...</div>
        </div>

        <p class="section-title">Position Calibration</p>
        <div class="steps">
          <div id="stepRight" class="step active">1. Capture a start point, move the controller to your right, then save <b>right</b>.</div>
          <div id="stepForward" class="step">2. Capture a start point, move forward, then save <b>forward</b>.</div>
          <div id="stepOrigin" class="step">3. Hold the controller at the neutral teleop origin, then save <b>origin</b>.</div>
        </div>
        <div id="nextAction" class="next-action">Start Quest streaming, then capture right direction.</div>
        <div class="actions">
          <button id="calibNext" class="primary">Start right sample</button>
          <button id="resetCalib">Restart calibration</button>
          <button id="clear" class="full">Clear path</button>
        </div>
        <div id="calibCard" class="card subtle">
          <div class="label">Frame status</div>
          <div id="calibStatus" class="value">No calibration yet.</div>
        </div>

        <p class="section-title">Rotation / Gripper Direction</p>
        <div class="card subtle">
          <div class="small">After position calibration, hold the controller in your neutral gripper pose, then save rotation. The gripper arrow should point down in the neutral pose.</div>
          <div class="profile-row" style="margin-top:8px;">
            <select id="gripperAxis" aria-label="Controller gripper arrow axis">
              <option value="-z">controller -Z as gripper arrow</option>
              <option value="+z">controller +Z as gripper arrow</option>
              <option value="+x">controller +X as gripper arrow</option>
              <option value="-x">controller -X as gripper arrow</option>
              <option value="+y">controller +Y as gripper arrow</option>
              <option value="-y">controller -Y as gripper arrow</option>
            </select>
            <button id="saveGripperAxis" disabled>Save arrow axis</button>
          </div>
          <div class="actions" style="margin-top:8px;">
            <button id="rotationMode">Show rotation view</button>
            <button id="saveRotation" disabled>Save neutral rotation</button>
            <button id="clearRotation" disabled>Clear rotation</button>
          </div>
          <div id="rotationStatus" class="value" style="margin-top:8px;">Position calibration required first.</div>
        </div>

        <p class="section-title">LIBERO Launch Settings</p>
        <div class="card subtle">
          <div class="label">Command for this profile</div>
          <pre id="liberoCommand" class="command-box">scripts/run_quest_tracker_hub.sh --profile libero_default
scripts/run_libero_teleop.sh --orientation</pre>
          <div class="small" style="margin-top:8px;">Stop this web tool before launching LIBERO; both bind the same Quest ports.</div>
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
          <button id="poseAxes">Show pose axes</button>
        </div>
        <div class="card subtle small">Drag to rotate. Wheel to zoom. Numeric teleop frame is [right, forward, up]. In the 3D view, forward is drawn into the scene (-Z) so right cross forward = up.</div>
      </aside>
    </div>
  </div>

<script>
const CALIBRATION_VERSION = 5;
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const dot = document.getElementById('dot');
const gateText = document.getElementById('gateText');
const statusText = document.getElementById('statusText');
const stageTitle = document.getElementById('stageTitle');
const streamMetric = document.getElementById('streamMetric');
const profileMetric = document.getElementById('profileMetric');
const nextActionEl = document.getElementById('nextAction');
const liberoCommandEl = document.getElementById('liberoCommand');
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
const calibNextBtn = document.getElementById('calibNext');
const profileSelect = document.getElementById('profileSelect');
const profileNameInput = document.getElementById('profileName');
const loadProfileBtn = document.getElementById('loadProfile');
const saveProfileBtn = document.getElementById('saveProfile');
const profileStatus = document.getElementById('profileStatus');
const rotationModeBtn = document.getElementById('rotationMode');
const saveRotationBtn = document.getElementById('saveRotation');
const clearRotationBtn = document.getElementById('clearRotation');
const gripperAxisSelect = document.getElementById('gripperAxis');
const saveGripperAxisBtn = document.getElementById('saveGripperAxis');
const rotationStatus = document.getElementById('rotationStatus');
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
let showPoseAxes = false;
let viewMode = 'path';
let dragging = false;
let dragStart = null;
const QUEST_UP = {x:0, y:1, z:0};
const STANDARD_GRIPPER_DOWN = {x:0, y:0, z:-1};
const MIN_RIGHT_M = 0.05;
let currentProfile = 'libero_default';
let profileInitialized = false;
let calibration = null;

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
function profileKey() { return `questTeleopCalibration:${currentProfile}`; }
function hasRightStart() { return calibration && calibration.rightStart; }
function hasRight() { return calibration && calibration.right; }
function hasForwardStart() { return calibration && calibration.forwardStart; }
function hasForward() { return calibration && calibration.forward; }
function hasOrigin() { return calibration && calibration.origin; }
function isCalibrated() { return calibration && calibration.origin && calibration.right && calibration.forward && calibration.up && calibration.version === CALIBRATION_VERSION; }
function hasRotationNeutral() { return isCalibrated() && calibration.rotation && calibration.rotation.neutralQuat; }
function gripperAxisName() { return (calibration && calibration.rotation && calibration.rotation.gripperAxis) || gripperAxisSelect.value || '-z'; }
function axisVec(name) {
  const sign = name[0] === '-' ? -1 : 1;
  const axis = name.slice(1);
  if (axis === 'x') return vec(sign,0,0);
  if (axis === 'y') return vec(0,sign,0);
  return vec(0,0,sign);
}
function sanitizeProfileName(name) {
  const cleaned = String(name || '').replace(/\.json$/i, '').replace(/[^A-Za-z0-9_.-]/g, '_').replace(/^_+|_+$/g, '');
  return cleaned || 'libero_default';
}
function localSaveCalibration() {
  if (!calibration) return;
  localStorage.setItem(profileKey(), JSON.stringify(calibration));
}
function loadLocalCalibration(profile=currentProfile) {
  try {
    const parsed = JSON.parse(localStorage.getItem(`questTeleopCalibration:${profile}`) || 'null');
    if (!parsed || parsed.version !== CALIBRATION_VERSION) return null;
    return parsed;
  } catch (_) { return null; }
}
function applyCalibration(data, profile=currentProfile, status='') {
  calibration = data && data.version === CALIBRATION_VERSION ? data : null;
  currentProfile = sanitizeProfileName(profile);
  profileNameInput.value = currentProfile;
  gripperAxisSelect.value = gripperAxisName();
  if (calibration) localSaveCalibration();
  refreshDisplayPoints(); updateStats(); draw();
  if (status) profileStatus.textContent = status;
}
function profileStateText() {
  if (!calibration) return 'No profile';
  if (!isCalibrated()) return 'Draft';
  return hasRotationNeutral() ? 'Ready + rotation' : 'Ready';
}
function updateLaunchCommand() {
  const profile = sanitizeProfileName(profileNameInput.value || currentProfile);
  const rotationFlag = hasRotationNeutral() ? ' --orientation' : '';
  liberoCommandEl.textContent = `# canonical ZMQ pipeline
scripts/run_quest_tracker_hub.sh --profile ${profile}
scripts/run_libero_teleop.sh --task-suite-name libero_spatial --task-id 0${rotationFlag}`;
}
function nextCalibrationAction() {
  if (!latestRaw && !isCalibrated()) return 'Start Quest streaming. Press B until Stream shows High, then begin calibration.';
  if (!hasRightStart()) return 'Capture a start point for the right-direction sample.';
  if (!hasRight()) return 'Move the controller to your physical right, hold still, then save right.';
  if (!hasForwardStart()) return 'Capture a start point for the forward-direction sample.';
  if (!hasForward()) return 'Move the controller forward, hold still, then save forward.';
  if (!hasOrigin()) return 'Hold the controller at the neutral teleop origin, then save origin.';
  if (!hasRotationNeutral()) return 'Position is ready. Optional: save the gripper arrow axis and neutral rotation.';
  return 'Profile is ready for LIBERO teleop.';
}
function updateToolSummary() {
  streamMetric.textContent = gateOpen ? 'Streaming' : (pauseState ? `Paused (${pauseState})` : 'Waiting');
  profileMetric.textContent = profileStateText();
  nextActionEl.textContent = nextCalibrationAction();
  stageTitle.textContent = viewMode === 'rotation' ? 'Rotation calibration view' : 'Trajectory calibration view';
  updateLaunchCommand();
}
function saveServerCalibration() {
  if (!isCalibrated()) {
    updateCalibrationStatus('Finish right, forward, then origin before saving a profile.');
    return Promise.resolve(false);
  }
  currentProfile = sanitizeProfileName(profileNameInput.value);
  calibration.profile = currentProfile;
  calibration.savedAt = new Date().toISOString();
  localSaveCalibration();
  return fetch(`/calibration?profile=${encodeURIComponent(currentProfile)}`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(calibration),
  }).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    profileStatus.textContent = `Saved profile: ${currentProfile}`;
    return loadProfiles().then(() => true);
  }).catch(err => {
    profileStatus.textContent = `Save failed: ${err.message}`;
    return false;
  });
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
function directionToTeleop(dir) {
  if (!isCalibrated()) return vec(dir.x, dir.z, dir.y);
  return vec(dot3(dir, calibration.right), dot3(dir, calibration.forward), dot3(dir, calibration.up));
}
function rawQuat(p) {
  return {x:Number(p.qx), y:Number(p.qy), z:Number(p.qz), w:Number(p.qw)};
}
function pointFromQuat(q) {
  return {qx:q.x, qy:q.y, qz:q.z, qw:q.w};
}
function quatNormalize(q) {
  const n = Math.hypot(q.x, q.y, q.z, q.w);
  return n > 1e-9 ? {x:q.x/n, y:q.y/n, z:q.z/n, w:q.w/n} : {x:0, y:0, z:0, w:1};
}
function quatConj(q) {
  return {x:-q.x, y:-q.y, z:-q.z, w:q.w};
}
function quatMul(a, b) {
  return {
    x: a.w*b.x + a.x*b.w + a.y*b.z - a.z*b.y,
    y: a.w*b.y - a.x*b.z + a.y*b.w + a.z*b.x,
    z: a.w*b.z + a.x*b.y - a.y*b.x + a.z*b.w,
    w: a.w*b.w - a.x*b.x - a.y*b.y - a.z*b.z
  };
}
function quatDeltaDeg(a, b) {
  const rel = quatNormalize(quatMul(quatConj(quatNormalize(a)), quatNormalize(b)));
  const w = Math.max(-1, Math.min(1, Math.abs(rel.w)));
  return 2 * Math.acos(w) * 180 / Math.PI;
}
function quatMatrixColumns(p) {
  let qx = Number(p.qx), qy = Number(p.qy), qz = Number(p.qz), qw = Number(p.qw);
  const n = Math.hypot(qx, qy, qz, qw);
  if (!Number.isFinite(n) || n <= 1e-9) return null;
  qx /= n; qy /= n; qz /= n; qw /= n;
  const xx=qx*qx, yy=qy*qy, zz=qz*qz, xy=qx*qy, xz=qx*qz, yz=qy*qz, wx=qw*qx, wy=qw*qy, wz=qw*qz;
  return [
    vec(1 - 2*(yy + zz), 2*(xy + wz), 2*(xz - wy)),
    vec(2*(xy - wz), 1 - 2*(xx + zz), 2*(yz + wx)),
    vec(2*(xz + wy), 2*(yz - wx), 1 - 2*(xx + yy))
  ];
}
function quatAxesTeleop(p) {
  const cols = quatMatrixColumns(p); if (!cols) return null;
  return [
    {label:'X', color:'#d44c47', dir:directionToTeleop(cols[0])},
    {label:'Y', color:'#448361', dir:directionToTeleop(cols[1])},
    {label:'Z', color:'#337ea9', dir:directionToTeleop(cols[2])}
  ];
}
function teleopToDisplayDir(dir) {
  return vec(dir.x, dir.z, -dir.y);
}
function teleopRotationMatrix(p) {
  const axes = quatAxesTeleop(p); if (!axes) return null;
  return [
    [axes[0].dir.x, axes[1].dir.x, axes[2].dir.x],
    [axes[0].dir.y, axes[1].dir.y, axes[2].dir.y],
    [axes[0].dir.z, axes[1].dir.z, axes[2].dir.z]
  ];
}
function matTranspose(a) {
  return [[a[0][0],a[1][0],a[2][0]],[a[0][1],a[1][1],a[2][1]],[a[0][2],a[1][2],a[2][2]]];
}
function matMul(a, b) {
  const out = [[0,0,0],[0,0,0],[0,0,0]];
  for (let r=0; r<3; r++) for (let c=0; c<3; c++) out[r][c] = a[r][0]*b[0][c] + a[r][1]*b[1][c] + a[r][2]*b[2][c];
  return out;
}
function matrixAxesTeleop(m) {
  return [
    {label:'X', color:'#d44c47', dir:vec(m[0][0], m[1][0], m[2][0])},
    {label:'Y', color:'#448361', dir:vec(m[0][1], m[1][1], m[2][1])},
    {label:'Z', color:'#337ea9', dir:vec(m[0][2], m[1][2], m[2][2])}
  ];
}
function matVec(m, v) {
  return vec(
    m[0][0]*v.x + m[0][1]*v.y + m[0][2]*v.z,
    m[1][0]*v.x + m[1][1]*v.y + m[1][2]*v.z,
    m[2][0]*v.x + m[2][1]*v.y + m[2][2]*v.z
  );
}
function relativeRotationMatrixTeleop() {
  if (!hasRotationNeutral() || !latestRaw) return null;
  const neutral = teleopRotationMatrix(pointFromQuat(calibration.rotation.neutralQuat));
  const current = teleopRotationMatrix(latestRaw);
  if (!neutral || !current) return null;
  return matMul(current, matTranspose(neutral));
}
function relativeRotationAxesTeleop() {
  const rel = relativeRotationMatrixTeleop();
  return rel ? matrixAxesTeleop(rel) : null;
}
function relativeGripperDirTeleop() {
  const rel = relativeRotationMatrixTeleop();
  return rel ? matVec(rel, STANDARD_GRIPPER_DOWN) : currentPhysicalGripperDirTeleop();
}
function currentPhysicalGripperDirTeleop() {
  const current = latestRaw ? teleopRotationMatrix(latestRaw) : null;
  const local = axisVec(gripperAxisName());
  return current ? matVec(current, local) : local;
}
function currentRightMotion() {
  if (!hasRightStart() || !latestRaw) return null;
  const rawDelta = sub(rawVec(latestRaw), calibration.rightStart);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  return {horizontal, length:norm(horizontal)};
}
function currentForwardMotion() {
  if (!hasRight() || !hasForwardStart() || !latestRaw) return null;
  const rawDelta = sub(rawVec(latestRaw), calibration.forwardStart);
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
  const cols = quatMatrixColumns(p); if (!cols) return null;
  return [
    {label:'X', color:'#d44c47', dir:directionToDisplay(cols[0])},
    {label:'Y', color:'#448361', dir:directionToDisplay(cols[1])},
    {label:'Z', color:'#337ea9', dir:directionToDisplay(cols[2])}
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
function drawOrientationAxesFromPoint(p, b, origin, axisLen, width, alpha, labelPrefix='') {
  const axes = quatAxes(p); if (!axes) return;
  for (const axis of axes) drawArrow3(origin, axis.dir, b, axis.color, axisLen, width, alpha, `${labelPrefix}${axis.label}`);
}
function drawTeleopAxes(axes, b, origin, axisLen, width, alpha, labelPrefix='') {
  if (!axes) return;
  for (const axis of axes) drawArrow3(origin, teleopToDisplayDir(axis.dir), b, axis.color, axisLen, width, alpha, `${labelPrefix}${axis.label}`);
}
function drawRotationView() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, rect.width, rect.height);
  const b = {xmin:-1,xmax:1,ymin:-1,ymax:1,zmin:-1,zmax:1,cx:0,cy:0,cz:0,span:2.2};
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  drawWorldAxes(b);
  const axisLen = 0.72;
  drawArrow3(vec(0,0,0), teleopToDisplayDir(STANDARD_GRIPPER_DOWN), b, '#111111', axisLen * 0.78, 2.0, 0.30, 'standard gripper down');
  if (hasRotationNeutral() && latestRaw) {
    drawTeleopAxes(relativeRotationAxesTeleop(), b, vec(0,0,0), axisLen, 4.0, 0.92, 'rel ');
    drawArrow3(vec(0,0,0), teleopToDisplayDir(relativeGripperDirTeleop()), b, '#111111', axisLen * 0.95, 5.0, 0.95, 'gripper');
  } else if (latestRaw) {
    drawTeleopAxes(quatAxesTeleop(latestRaw), b, vec(0,0,0), axisLen, 3.0, 0.75, 'abs ');
    drawArrow3(vec(0,0,0), teleopToDisplayDir(currentPhysicalGripperDirTeleop()), b, '#111111', axisLen * 0.95, 5.0, 0.75, 'gripper');
  }
  ctx.save();
  ctx.fillStyle = '#111';
  ctx.font = '13px Helvetica Neue, Helvetica, Arial, sans-serif';
  ctx.fillText('Rotation view: current controller relative to saved neutral', 14, 24);
  ctx.fillStyle = '#666';
  ctx.fillText('After Save neutral rotation, the black gripper arrow should overlap standard gripper down.', 14, 44);
  if (hasRotationNeutral() && latestRaw) {
    ctx.fillText(`delta from neutral: ${fmt(quatDeltaDeg(calibration.rotation.neutralQuat, rawQuat(latestRaw)), 1)} deg`, 14, 64);
  } else {
    ctx.fillText('No neutral saved yet: showing absolute controller axes.', 14, 64);
  }
  ctx.restore();
}
function draw() {
  const rect = canvas.getBoundingClientRect();
  if (viewMode === 'rotation') { drawRotationView(); return; }
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
  stepRight.classList.toggle('done', hasRight());
  stepRight.classList.toggle('active', !hasRight());
  stepForward.classList.toggle('done', hasForward());
  stepForward.classList.toggle('active', hasRight() && !hasForward());
  stepOrigin.classList.toggle('done', isCalibrated());
  stepOrigin.classList.toggle('active', hasForward() && !hasOrigin());
  saveProfileBtn.disabled = !isCalibrated();
  calibNextBtn.disabled = !latestRaw && !isCalibrated();
  if (!hasRightStart()) calibNextBtn.textContent = 'Start right sample';
  else if (!hasRight()) calibNextBtn.textContent = 'Save right';
  else if (!hasForwardStart()) calibNextBtn.textContent = 'Start forward sample';
  else if (!hasForward()) calibNextBtn.textContent = 'Save forward';
  else if (!hasOrigin()) calibNextBtn.textContent = 'Save origin';
  else calibNextBtn.textContent = 'Start new calibration';
  if (message) { calibStatus.textContent = message; return; }
  if (isCalibrated()) {
    calibStatus.textContent = `Ready: ${currentProfile}\nright=[${fmt(calibration.right.x,3)}, ${fmt(calibration.right.y,3)}, ${fmt(calibration.right.z,3)}]\nforward=[${fmt(calibration.forward.x,3)}, ${fmt(calibration.forward.y,3)}, ${fmt(calibration.forward.z,3)}]\norigin=[${fmt(calibration.origin.x,3)}, ${fmt(calibration.origin.y,3)}, ${fmt(calibration.origin.z,3)}]`;
  } else if (hasForward()) {
    calibStatus.textContent = 'Right and forward are set. Hold the controller at the neutral teleop origin and click Save origin.';
  } else if (hasForwardStart()) {
    calibStatus.textContent = `Move forward and hold.\nCurrent ${forwardMotionText}\nNeed at least ${fmt(MIN_RIGHT_M,2)} m.`;
  } else if (hasRight()) {
    calibStatus.textContent = 'Right saved. Click Start forward sample from any comfortable point, then move forward.';
  } else if (hasRightStart()) {
    calibStatus.textContent = `Move to your right and hold.\nCurrent ${rightMotionText}\nNeed at least ${fmt(MIN_RIGHT_M,2)} m.`;
  } else {
    calibStatus.textContent = latestRaw ? 'Click Start right sample, move right, then Save right.' : 'Start Quest streaming first, then calibrate.';
  }
}
function updateRotationStatus(message=null) {
  saveRotationBtn.disabled = !isCalibrated() || !latestRaw;
  clearRotationBtn.disabled = !hasRotationNeutral();
  saveGripperAxisBtn.disabled = !isCalibrated();
  rotationModeBtn.textContent = viewMode === 'rotation' ? 'Show path view' : 'Show rotation view';
  gripperAxisSelect.value = gripperAxisName();
  if (message) { rotationStatus.textContent = message; return; }
  if (!isCalibrated()) {
    rotationStatus.textContent = 'Finish position calibration first.';
    return;
  }
  if (!latestRaw) {
    rotationStatus.textContent = 'Waiting for controller pose.';
    return;
  }
  const axes = hasRotationNeutral() ? relativeRotationAxesTeleop() : quatAxesTeleop(latestRaw);
  const axisLabel = hasRotationNeutral() ? 'relative axes' : 'absolute axes';
  const axisText = axes ? axes.map(a => `${a.label}=[${fmt(a.dir.x,2)}, ${fmt(a.dir.y,2)}, ${fmt(a.dir.z,2)}]`).join('\n') : '--';
  const grip = hasRotationNeutral() ? relativeGripperDirTeleop() : currentPhysicalGripperDirTeleop();
  const gripText = `gripper arrow(${gripperAxisName()})=[${fmt(grip.x,2)}, ${fmt(grip.y,2)}, ${fmt(grip.z,2)}] target=[0.00, 0.00, -1.00]`;
  const delta = hasRotationNeutral() ? `\ndelta from neutral=${fmt(quatDeltaDeg(calibration.rotation.neutralQuat, rawQuat(latestRaw)), 1)} deg` : '';
  const neutral = hasRotationNeutral() ? `Neutral saved: ${calibration.rotation.capturedAt || 'yes'}` : 'No neutral rotation saved yet.';
  rotationStatus.textContent = `${neutral}${delta}\n${gripText}\ncurrent ${axisLabel} in [right, forward, up]:\n${axisText}`;
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
  updateRotationStatus();
  updateToolSummary();
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
function beginCalibration() {
  currentProfile = sanitizeProfileName(profileNameInput.value);
  const p = requireLatest(); if (!p) return;
  calibration = {version:CALIBRATION_VERSION, profile:currentProfile, rightStart:p, up:QUEST_UP, state:'right_started', createdAt:new Date().toISOString()};
  localSaveCalibration(); refreshDisplayPoints(); updateStats(); draw();
}
function saveRightDirection() {
  const p = requireLatest(); if (!p) return;
  if (!hasRightStart()) { updateCalibrationStatus('Click Start right sample first.'); return; }
  const rawDelta = sub(p, calibration.rightStart);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  const length = norm(horizontal);
  if (length < MIN_RIGHT_M) { updateCalibrationStatus(`Right movement too small (${fmt(length,3)} m). Move farther right and save again.`); return; }
  calibration.rightEnd = p;
  calibration.right = normalize(horizontal);
  calibration.state = 'right_ready';
  localSaveCalibration(); refreshDisplayPoints(); updateStats(); draw();
}
function startForwardSample() {
  const p = requireLatest(); if (!p) return;
  if (!hasRight()) { updateCalibrationStatus('Save right first.'); return; }
  calibration.forwardStart = p;
  calibration.state = 'forward_started';
  localSaveCalibration(); refreshDisplayPoints(); updateStats(); draw();
}
function saveForwardDirection() {
  const p = requireLatest(); if (!p) return;
  if (!hasRight() || !hasForwardStart()) { updateCalibrationStatus('Click Start forward sample first.'); return; }
  const rawDelta = sub(p, calibration.forwardStart);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  const orthogonal = sub(horizontal, mul(calibration.right, dot3(horizontal, calibration.right)));
  const length = norm(orthogonal);
  if (length < MIN_RIGHT_M) { updateCalibrationStatus(`Forward movement too small (${fmt(length,3)} m). Move farther forward and save again.`); return; }
  let forward = normalize(cross(QUEST_UP, calibration.right));
  if (dot3(forward, orthogonal) < 0) forward = mul(forward, -1);
  calibration.forwardEnd = p;
  calibration.forwardHint = normalize(horizontal);
  calibration.forward = forward;
  calibration.state = 'forward_ready';
  localSaveCalibration(); refreshDisplayPoints(); updateStats(); draw();
}
function saveOrigin() {
  const p = requireLatest(); if (!p) return;
  if (!hasForward()) { updateCalibrationStatus('Save forward first.'); return; }
  calibration.origin = p;
  calibration.state = 'ready';
  calibration.completedAt = new Date().toISOString();
  localSaveCalibration(); refreshDisplayPoints(); updateStats(); draw();
  saveServerCalibration();
}
calibNextBtn.onclick = () => {
  if (isCalibrated()) { calibration = null; beginCalibration(); return; }
  if (!hasRightStart()) beginCalibration();
  else if (!hasRight()) saveRightDirection();
  else if (!hasForwardStart()) startForwardSample();
  else if (!hasForward()) saveForwardDirection();
  else if (!hasOrigin()) saveOrigin();
};
document.getElementById('resetCalib').onclick = () => {
  calibration = null;
  localStorage.removeItem(profileKey());
  refreshDisplayPoints(); updateStats(); draw();
};
document.getElementById('clear').onclick = () => { rawPoints = []; refreshDisplayPoints(); updateStats(); draw(); };
document.getElementById('fit').onclick = () => { zoom = 1; draw(); };
poseAxesBtn.onclick = () => { showPoseAxes = !showPoseAxes; poseAxesBtn.textContent = showPoseAxes ? 'Hide pose axes' : 'Show pose axes'; draw(); };
rotationModeBtn.onclick = () => {
  viewMode = viewMode === 'rotation' ? 'path' : 'rotation';
  updateRotationStatus(); updateToolSummary(); draw();
};
saveRotationBtn.onclick = () => {
  if (!isCalibrated()) { updateRotationStatus('Finish position calibration before saving rotation.'); return; }
  if (!latestRaw) { updateRotationStatus('No controller sample yet.'); return; }
  calibration.rotation = {
    version: 1,
    neutralQuat: rawQuat(latestRaw),
    gripperAxis: gripperAxisSelect.value,
    capturedAt: new Date().toISOString(),
    mode: 'controller-neutral-to-initial-libero-eef'
  };
  localSaveCalibration(); updateStats(); draw();
  saveServerCalibration();
};
clearRotationBtn.onclick = () => {
  if (calibration && calibration.rotation) {
    delete calibration.rotation;
    localSaveCalibration(); updateStats(); draw();
    saveServerCalibration();
  }
};
saveGripperAxisBtn.onclick = () => {
  if (!isCalibrated()) { updateRotationStatus('Finish position calibration before saving gripper axis.'); return; }
  calibration.rotation = calibration.rotation || {version: 1, mode: 'controller-neutral-to-initial-libero-eef'};
  calibration.rotation.gripperAxis = gripperAxisSelect.value;
  localSaveCalibration(); updateStats(); draw();
  saveServerCalibration();
};
gripperAxisSelect.addEventListener('change', () => {
  if (calibration && calibration.rotation) calibration.rotation.gripperAxis = gripperAxisSelect.value;
  updateRotationStatus(); draw();
});
profileNameInput.addEventListener('change', () => {
  currentProfile = sanitizeProfileName(profileNameInput.value);
  profileNameInput.value = currentProfile;
  const local = loadLocalCalibration(currentProfile);
  if (local) applyCalibration(local, currentProfile, `Loaded local draft: ${currentProfile}`);
  else profileStatus.textContent = `Profile name set: ${currentProfile}`;
  updateToolSummary();
});
profileSelect.addEventListener('change', () => {
  if (profileSelect.value) {
    profileNameInput.value = profileSelect.value;
    loadServerCalibration(profileSelect.value);
  }
});
loadProfileBtn.onclick = () => loadServerCalibration(profileNameInput.value);
saveProfileBtn.onclick = () => saveServerCalibration();
canvas.addEventListener('mousedown', e => { dragging = true; dragStart = {x:e.clientX, y:e.clientY, az, el}; });
window.addEventListener('mouseup', () => dragging = false);
window.addEventListener('mousemove', e => {
  if (!dragging) return;
  az = dragStart.az + (e.clientX - dragStart.x) * 0.01;
  el = Math.max(-1.2, Math.min(1.2, dragStart.el + (e.clientY - dragStart.y) * 0.01));
  draw();
});
canvas.addEventListener('wheel', e => { e.preventDefault(); zoom *= Math.exp(-e.deltaY * 0.001); zoom = Math.max(.25, Math.min(6, zoom)); draw(); }, {passive:false});
function loadProfiles() {
  return fetch('/calibrations').then(r => r.json()).then(info => {
    const activeProfile = sanitizeProfileName(info.active || currentProfile);
    if (!profileInitialized) {
      currentProfile = activeProfile;
      profileInitialized = true;
    } else {
      currentProfile = sanitizeProfileName(currentProfile || activeProfile);
    }
    profileNameInput.value = currentProfile;
    profileSelect.innerHTML = '';
    const names = (info.profiles || []).map(p => p.name);
    if (!names.includes(currentProfile)) names.unshift(currentProfile);
    for (const name of names) {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      opt.selected = name === currentProfile;
      profileSelect.appendChild(opt);
    }
    profileStatus.textContent = names.length ? `Profiles: ${names.length}` : 'No saved profiles yet.';
    return info;
  }).catch(err => {
    profileStatus.textContent = `Profile list failed: ${err.message}`;
    return {active: currentProfile, profiles: []};
  });
}
function loadServerCalibration(profile=currentProfile) {
  currentProfile = sanitizeProfileName(profile);
  profileNameInput.value = currentProfile;
  return fetch(`/calibration?profile=${encodeURIComponent(currentProfile)}`).then(r => r.ok ? r.json() : null).then(saved => {
    if (saved && saved.version === CALIBRATION_VERSION) {
      applyCalibration(saved, currentProfile, `Loaded profile: ${currentProfile}`);
    } else {
      const local = loadLocalCalibration(currentProfile);
      applyCalibration(local, currentProfile, local ? `Loaded local draft: ${currentProfile}` : `No saved profile: ${currentProfile}`);
    }
  }).catch(err => {
    const local = loadLocalCalibration(currentProfile);
    applyCalibration(local, currentProfile, local ? `Loaded local draft after server error: ${currentProfile}` : `Load failed: ${err.message}`);
  });
}
loadProfiles().then(() => loadServerCalibration(currentProfile));
fetch('/snapshot').then(r => r.json()).then(handleEvent).catch(() => {});
const es = new EventSource('/events');
es.onmessage = e => handleEvent(JSON.parse(e.data));
es.onerror = () => { gateText.textContent = 'disconnected'; dot.classList.remove('on'); };
updateCalibrationStatus(); updateRotationStatus(); resize(); setInterval(draw, 1000);
</script>
</body>
</html>
"""
class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

def make_handler(state: LiveState, calibration_path: Path) -> type[BaseHTTPRequestHandler]:
    calibration_dir = calibration_path.parent
    default_profile = calibration_path.stem

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

        def selected_calibration_path(self) -> Path:
            query = parse_qs(urlparse(self.path).query)
            profile = sanitize_profile(query.get("profile", [default_profile])[0], default_profile)
            return calibration_file(calibration_dir, profile)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                self.send_text(HTML, "text/html; charset=utf-8")
                return
            if path == "/snapshot":
                self.send_text(json.dumps(state.snapshot(), separators=(",", ":")), "application/json")
                return
            if path == "/calibrations":
                profiles = []
                calibration_dir.mkdir(parents=True, exist_ok=True)
                for item in sorted(calibration_dir.glob("*.json")):
                    try:
                        data = json.loads(item.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        data = None
                    stat = item.stat()
                    profiles.append(
                        {
                            "name": item.stem,
                            "path": str(item),
                            "mtime": stat.st_mtime,
                            "version": data.get("version") if isinstance(data, dict) else None,
                            "complete": calibration_complete(data),
                        }
                    )
                self.send_text(
                    json.dumps({"active": default_profile, "profiles": profiles}, separators=(",", ":")),
                    "application/json",
                )
                return
            if path == "/calibration":
                try:
                    selected = self.selected_calibration_path()
                    if selected.exists():
                        self.send_text(selected.read_text(encoding="utf-8"), "application/json")
                    else:
                        self.send_text("null", "application/json")
                except ValueError as exc:
                    self.send_text(str(exc), "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
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

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/calibration":
                self.send_text("Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                for key in ("origin", "right", "forward", "up"):
                    if key not in data:
                        raise ValueError(f"missing {key}")
                selected = self.selected_calibration_path()
                selected.parent.mkdir(parents=True, exist_ok=True)
                selected.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                self.send_text(f"Invalid calibration: {exc}", "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return
            self.send_text(json.dumps({"ok": True}, separators=(",", ":")), "application/json")

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/calibration":
                self.send_text("Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            try:
                self.selected_calibration_path().unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                self.send_text(f"Could not delete calibration: {exc}", "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return
            self.send_text(json.dumps({"ok": True}, separators=(",", ":")), "application/json")

    return Handler
