#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
EMBODIED_OPS_ROOT="${EMBODIED_OPS_ROOT:-$(cd ../embodied-ops && pwd)}"
if command -v uv >/dev/null 2>&1; then
  [[ -x .venv/bin/python ]] || uv venv --python 3.11 .venv
  uv pip install --python .venv/bin/python -e "${EMBODIED_OPS_ROOT}[teleop-zmq]"
  uv pip install --python .venv/bin/python -e '.[dev,foxglove]' ruff
else
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e "${EMBODIED_OPS_ROOT}[teleop-zmq]"
  .venv/bin/python -m pip install -e '.[dev,foxglove]' ruff
fi
echo "Installed. Activate with: source .venv/bin/activate"
