"""Run the shipped page's JavaScript handlers with a small deterministic DOM."""

import shutil
import subprocess
from pathlib import Path

import pytest

from quest_trajectory_recorder.live3d_web import HTML


def test_actual_page_action_feedback():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the page handler regression test")
    script = HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("web_feedback.cjs"))],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
