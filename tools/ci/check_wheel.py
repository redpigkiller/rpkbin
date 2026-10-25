"""Check a built wheel in a fresh venv, outside the source checkout.

Usage: python tools/ci/check_wheel.py dist
The driver uses only the standard library; pip installs declared dependencies.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import os
from pathlib import Path
import subprocess
import tempfile
import venv
from zipfile import ZipFile


# This code runs in the newly created interpreter, not the developer's venv.
SMOKE = r"""
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
import subprocess
import sys

flavor, expected_version = sys.argv[1:]
prefix = Path(sys.prefix).resolve()
assert importlib.metadata.version("rpkbin") == expected_version
for name in ("rpkbin", "rpkbin.mapbv", "rpkbin.tablepack", "rpkbin.job_manager", "rpkbin.wave"):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(prefix), (name, module.__file__)

from rpkbin.mapbv import var
reg = var("sample", 16, 0x1234)
reg[15:8].value = 0xAB
assert reg.value == 0xAB34

from rpkbin.tablepack import pack, unpack
rows = [{"name": "alpha", "value": 2**100}, {"name": "beta", "value": 7}]
assert unpack(pack(rows)) == rows
assert unpack(pack(rows, compress="zlib")) == rows

scripts = prefix / ("Scripts" if sys.platform == "win32" else "bin")
suffix = ".exe" if sys.platform == "win32" else ""
def cli(name, *args):
    result = subprocess.run(
        [str(scripts / (name + suffix)), *args],
        text=True, encoding="utf-8", capture_output=True, timeout=60,
    )
    if result.returncode:
        print(result.stdout, end="", file=sys.stderr)
        print(result.stderr, end="", file=sys.stderr)
        result.check_returncode()
    return result.stdout

assert "rpk-wave" in cli("rpk-wave", "--help")
assert "rpk-calc" in cli("rpk-calc", "--help")
Path("work.py").write_text(
    "import sys\nfrom rpkbin.wave import Job, JobManager, Expect\n"
    "mgr = JobManager(max_workers=1)\n"
    "job = mgr.add(Job('smoke', [sys.executable, '-u', '-c', \"print('SMOKE_OK')\"]))\n"
    "job.check('marker', match=r'^SMOKE_OK$', expect=Expect.PRESENT)\n",
    encoding="utf-8",
)
output = cli("rpk-wave", "work.py", "--headless")
assert "SMOKE_OK" in output and "PASS" in output, output

if flavor == "base":
    for name in ("brotli", "apytypes", "PySide6", "qtconsole"):
        assert importlib.util.find_spec(name) is None, name
else:
    import brotli
    assert unpack(pack(rows, compress="brotli")) == rows
    for name in ("rpkbin.calc.core.session", "rpkbin.calc.ui.app"):
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(prefix), (name, module.__file__)
    from rpkbin.calc.core import CalcConfig, WorkspaceSession
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    assert not session.execute("gain = 3\ndef scale(x):\n    return gain * x").error_text
    assert not session.execute("answer = scale(7)").error_text
    assert session.user_ns["answer"] == 21
    assert not session.execute("n = -255\ns = np.float32(1.5)\nf = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)").error_text
    assert session.get_inspector_snapshot("n").native_bits["hex"] == "-0xff"
    assert session.get_inspector_snapshot("s").native_bits["hex"] == "0x3FC00000"
    from rpkbin.calc.core.editing import EditJournal
    journal = EditJournal(session)
    journal.apply([("f", None)], lambda: session.toggle_target_bit("f", None, 7))
    assert session.get_inspector_snapshot("f").native_bits["hex"] == "0x9E"
    assert session.user_ns["f"].bias == 7
    journal.replay()
    assert session.user_ns["f"].to_bits() == 0x1E
    # Artwork must also load from the installed wheel, outside the source tree.
    from PySide6.QtWidgets import QApplication
    from rpkbin.calc.ui.icons import application_icon
    app = QApplication.instance() or QApplication([])
    icon = application_icon()
    assert not icon.isNull()
    assert {16, 32, 256, 512} <= {size.width() for size in icon.availableSizes()}
    assert not icon.pixmap(32, 32).isNull()
    # Verify calculation inside the REPL rather than its answer-label formatting.
    output = cli("rpk-calc", "--eval", "assert ((0x12 << 4) | 3) == 291; print('CALC_SMOKE_OK')")
    assert "CALC_SMOKE_OK" in output, output

print(f"Installed {flavor} smoke passed: rpkbin {expected_version}")
"""


def check_wheel(dist_dir: Path) -> None:
    wheels = list(dist_dir.resolve().glob("rpkbin-*.whl"))
    if len(wheels) != 1:
        raise ValueError(
            f"Expected exactly one rpkbin wheel in {dist_dir}, found {len(wheels)}"
        )
    wheel = wheels[0]
    with ZipFile(wheel) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError(
                "Wheel must contain exactly one distribution metadata file"
            )
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        version = metadata["Version"]
        if metadata["Name"] != "rpkbin" or not version:
            raise ValueError("Expected rpkbin distribution metadata with a version")

    with tempfile.TemporaryDirectory(prefix="rpkbin-wheel-") as directory:
        root = Path(directory)
        env_dir = root / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        work = root / "work"
        work.mkdir()
        # Do not load the developer's Calc preferences or startup script.
        home = root / "home"
        home.mkdir()
        env = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        # Keep pip's normal cache (including setup-python's restored cache).
        # Only the application checks need an isolated configuration directory.
        smoke_env = dict(env, HOME=str(home), USERPROFILE=str(home))
        for flavor, extra in (("base", ""), ("all", "[all]")):
            print(f"Checking {wheel.name}: {flavor}", flush=True)
            subprocess.run(
                [
                    str(python),
                    "-X",
                    "utf8",
                    "-I",
                    "-m",
                    "pip",
                    "install",
                    str(wheel) + extra,
                ],
                cwd=work,
                env=env,
                check=True,
                timeout=900,
            )
            subprocess.run(
                [str(python), "-X", "utf8", "-I", "-m", "pip", "check"],
                cwd=work,
                env=env,
                check=True,
                timeout=60,
            )
            subprocess.run(
                [str(python), "-X", "utf8", "-I", "-c", SMOKE, flavor, version],
                cwd=work,
                env=smoke_env,
                check=True,
                timeout=180,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist_dir", type=Path, help="Directory containing one built rpkbin wheel"
    )
    check_wheel(parser.parse_args().dist_dir)
