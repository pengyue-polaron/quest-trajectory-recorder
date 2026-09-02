#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
EMBODIED_OPS_ROOT="${EMBODIED_OPS_ROOT:-$ROOT/../embodied-ops}"
if [[ ! -f "$EMBODIED_OPS_ROOT/pyproject.toml" ]]; then
  echo "Clone embodied-ops beside this repository or set EMBODIED_OPS_ROOT." >&2
  exit 1
fi
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
