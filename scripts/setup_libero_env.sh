#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENPI_ROOT="${OPENPI_ROOT:-/Users/pengyue/Codespace/openpi_cam}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"

cd "$ROOT"
uv venv --python "$PYTHON_VERSION" .venv-libero
source .venv-libero/bin/activate
uv pip install -e .
uv pip install \
  "numpy>=1.23,<2" \
  pyzmq pyyaml easydict future cloudpickle gym==0.25.2 \
  matplotlib opencv-python imageio tqdm tyro \
  robosuite==1.4.1 bddl==1.0.1 torch torchvision
uv pip install -e "$OPENPI_ROOT/third_party/libero"
"$ROOT/.venv-libero/bin/python" - <<'PY' || true
from pathlib import Path
import shutil
import robosuite
base = Path(robosuite.__path__[0])
private = base / "macros_private.py"
if not private.exists():
    shutil.copyfile(base / "macros.py", private)
PY

cat <<EOF
LIBERO teleop env ready.
Run:
  scripts/run_libero_teleop.sh --task-suite-name libero_spatial --task-id 0
EOF
