"""Run a real Job in a fresh interpreter with composition imports blocked."""

import subprocess
import sys
from pathlib import Path

from tools.ci.check_wheel import CORE_SMOKE


def test_bare_job_with_composition_imports_blocked(tmp_path):
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1])\n" + CORE_SMOKE, str(root)],
        cwd=tmp_path, check=True, timeout=30,
    )
