"""The installed Flow engine can run with all orchestration imports blocked."""

import subprocess
import sys
from pathlib import Path

from tools.ci.check_wheel import FLOW_SMOKE


def test_flow_session_with_orchestration_imports_blocked():
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1])\n" + FLOW_SMOKE, str(root)],
        cwd=root, check=True, timeout=30,
    )
