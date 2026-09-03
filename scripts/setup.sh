#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if command -v uv >/dev/null 2>&1; then
  [[ -x .venv/bin/python ]] || uv venv --python 3.11 .venv
  uv pip install --python .venv/bin/python -e '.[dev,foxglove]' ruff
else
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e '.[dev,foxglove]' ruff
fi
if [[ -n "${EMBODIED_OPS_ROOT:-}" ]]; then
  if [[ ! -f "${EMBODIED_OPS_ROOT}/pyproject.toml" ]]; then
    echo "EMBODIED_OPS_ROOT does not contain a Python project: ${EMBODIED_OPS_ROOT}" >&2
    exit 1
  fi
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -e "${EMBODIED_OPS_ROOT}[teleop-zmq]"
  else
    .venv/bin/python -m pip install -e "${EMBODIED_OPS_ROOT}[teleop-zmq]"
  fi
fi
echo "Installed. Activate with: source .venv/bin/activate"
