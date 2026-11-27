"""Check a built wheel in a fresh venv, outside the source checkout.

Usage: python tools/ci/check_wheel.py dist
The driver uses only the standard library; pip installs declared dependencies.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


CORE_SMOKE = r"""
import importlib.abc
import os
from pathlib import Path
import sys
import tempfile

blocked = ("rpkbin.flow", "rpkbin.wave", "rpkbin.job_manager")


def forbidden(name):
    return any(name == package or name.startswith(package + ".") for package in blocked)


class BlockComposition(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if forbidden(fullname):
            raise AssertionError(f"Unexpected composition import: {fullname}")


assert not any(map(forbidden, sys.modules))
sys.meta_path.insert(0, BlockComposition())
from rpkbin.jobcore import Job, JobManager, JobStatus, ProcessVerdict

with tempfile.TemporaryDirectory(dir=".") as directory:
    manager = JobManager(log_dir=directory)
    try:
        child = (
            "import os; "
            f"assert os.isatty(0) is {os.name == 'posix'!r}; "
            "print('SMOKE_OK')"
        )
        job = manager.add(Job("standalone", [sys.executable, "-u", "-c", child], pty=os.name == "posix"))
        manager.start()
        assert manager._drain(15)
        assert job.status is JobStatus.DONE and job.returncode == 0
        assert job.result.verdict is ProcessVerdict.NA
        assert job.logs() == ["SMOKE_OK"]
        assert Path(directory, f"{job.id}.log").read_text(encoding="utf-8").endswith("SMOKE_OK\n")
        assert manager.snapshot()["jobs"][0]["finalized"]
    finally:
        manager.close()
assert not any(map(forbidden, sys.modules))
"""


FLOW_SMOKE = r"""
import importlib.abc
import sys

blocked = ("rpkbin.job_manager", "rpkbin.jobcore", "rpkbin.wave")


def forbidden(name):
    return any(name == package or name.startswith(package + ".") for package in blocked)


class BlockOrchestration(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if forbidden(fullname):
            raise AssertionError(f"Unexpected orchestration import: {fullname}")


assert not any(map(forbidden, sys.modules))
sys.meta_path.insert(0, BlockOrchestration())
from rpkbin.flow import ActionOutcome, Analyzer, Automation, Binding, Flow, FlowEvent, FlowSession, Parser, Result

actions, events, publications = [], [], []


def react(event, ctx):
    assert isinstance(event, FlowEvent)
    events.append(event.name)
    if event.name == "started":
        return ctx.actions.send("probe")


def send(text):
    assert publications[-1]["data"]["phase"] == "requested"
    actions.append(text)
    return ActionOutcome("accepted", "delivered")


def finish(end, ctx):
    ctx.summary.result(Result.PASS, final=end.complete)


parser = Parser("numbers", lambda line, ctx: {"value": int(line)})
analyzer = Analyzer("verify", records=[Binding(parser, lambda record, ctx: None)], on_finish=finish)
flow = Flow("standalone", parsers=[parser], analyzers=[analyzer], result=analyzer,
            automations=[Automation("observe", on_event=react)])
session = FlowSession(flow, automation_enabled=True, control={"send": send}, publish=publications.append)
session.start()
assert session.wait_automation()
session.feed("42")
session.finish()
assert session.wait_automation()
snapshot = session.snapshot()
assert snapshot["result"] == Result.PASS
assert snapshot["parsers"][0]["latest"] == {"value": 42}
assert snapshot["automation_error_count"] == 0
assert events == ["started", "finished"] and actions == ["probe"]
assert [row["data"]["phase"] for row in publications if row["kind"] == "action"] == ["requested", "returned"]
assert not any(map(forbidden, sys.modules))
"""



# This code runs in the newly created interpreter, not the developer's venv.
SMOKE = r"""
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urljoin
from urllib.request import urlopen

flavor, expected_version = sys.argv[1:]
prefix = Path(sys.prefix).resolve()
assert importlib.metadata.version("rpkbin") == expected_version
assert importlib.util.find_spec("rpkbin.job_manager") is None
process = importlib.import_module("rpkbin.jobcore._process")
pty_helper = Path(process.__file__).resolve().with_name("_pty_exec.py")
assert pty_helper.is_relative_to(prefix) and pty_helper.is_file(), pty_helper
import rpkbin.mcucc.cli

assert not any(name == "rpkbin.isasim" or name.startswith("rpkbin.isasim.") for name in sys.modules)
mcucc_dir = Path(rpkbin.mcucc.__file__).resolve().parent
assert mcucc_dir.is_relative_to(prefix), mcucc_dir
for name in ("rpkbin", "rpkbin.mapbv", "rpkbin.tablepack", "rpkbin.jobcore", "rpkbin.flow", "rpkbin.wave", "rpkbin.wave.archive", "rpkbin.wave.sources", "rpkbin.isasim", "rpkbin.app.isasim_gui"):
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
assert "rpk-isasim" in cli("rpk-isasim", "--help")
assert "rpk-isasim-gui" in cli("rpk-isasim-gui", "--help")
Path("isa.py").write_text(
    "from rpkbin.isasim import MCU\n"
    "mcu = MCU('installed')\n"
    "mcu.space('prog', cell_bits=8, size=4, access='ro', "
    "reset_state='initialized', init=0, is_program=True)\n"
    "mcu.reg('cursor', bits=3, reset=0, is_pc=True)\n"
    "mcu.on_undefined_opcode('stop')\n"
    "mcu.on_out_of_range('stop')\n"
    "mcu.on_write_to_readonly('stop')\n"
    "mcu.on_uninitialized_read('stop')\n"
    "@mcu.instr('00000000', syntax='END', cycles=1)\n"
    "def finish(st): st.halt('done')\n",
    encoding="utf-8",
)
from rpkbin.isasim import Simulator, Snapshot, load_mcu
sim = Simulator(load_mcu("isa.py"))
result = sim.run(max_steps=1)
assert (result.stop_reason, result.steps, result.cycles, result.pc) == ("halted", 1, 1, 1)
sim.snapshot().save("state.json")
sim.reset()
sim.restore(Snapshot.load("state.json"))
assert (sim.cycle, sim.state.pc) == (1, 1)
isa_output = cli("rpk-isasim", "run", "isa.py", "--max-steps", "1")
module_output = subprocess.run(
    [sys.executable, "-I", "-m", "rpkbin.isasim", "run", "isa.py", "--max-steps", "1"],
    capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
).stdout
assert isa_output == module_output
assert "RESULT stop_reason=halted steps=1 instructions=1 cycles=1 pc=0x1" in isa_output

def mcucc_cli(*args):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-I", "-B", "-m", "rpkbin.mcucc", *args],
        encoding="utf-8", capture_output=True, timeout=60,
    )
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result

for relative in ("README.md", "AI-GUIDE.md", "CHANGELOG.md", "doc/errors.md",
                 "doc/guarantees.md", "doc/language.md", "doc/target.md", "examples/README.md"):
    assert (mcucc_dir / relative).is_file(), relative
examples = mcucc_dir / "examples"
target = str(examples / "target.py.txt")
source = str(examples / "program.py.txt")
compile_args = (source, "--target", target, "--spill-pool", "data:0x60-0x6f")
help_output = mcucc_cli("--help").stdout
assert all(command in help_output for command in ("lint-target", "check-forms", "compile", "verify"))
assert mcucc_cli("lint-target", target).stdout == (examples / "lint.tsv").read_text(encoding="utf-8")
assert "verified: forms" in mcucc_cli("check-forms", target, "--seeds", "1").stdout
compiled = mcucc_cli("compile", *compile_args, "--no-comments", "--report")
assert compiled.stdout == (examples / "program.asm").read_text(encoding="utf-8")
assert json.loads(compiled.stderr) == json.loads((examples / "report.json").read_text(encoding="utf-8"))
assert "add2: verified" in mcucc_cli("verify", *compile_args, "--window", "256").stdout

Path("work.py").write_text(
    '''
import sys
from rpkbin.flow import Analyzer, Binding, Flow, Parser
from rpkbin.jobcore import Job, JobManager
from rpkbin.wave import WaveWorkspace


def parse_marker(line, ctx):
    if line == "SMOKE_OK":
        return {"passed": True}


def remember_marker(record, ctx):
    ctx.state["seen"] = record["passed"]


def finish(end, ctx):
    passed = end.complete and end.execution.get("returncode") == 0 and ctx.state.get("seen", False)
    ctx.summary.result("PASS" if passed else "FAIL", reason="marker and exit status", final=end.complete)


marker = Parser("marker", parse_marker)
verification = Analyzer("verification", records=[Binding(marker, remember_marker)], on_finish=finish)
flow = Flow("verification", parsers=[marker], analyzers=[verification], result=verification)
workspace = WaveWorkspace(JobManager(max_workers=2))
for name in ("smoke",):
    workspace.add(Job(name, [sys.executable, "-u", "-c", "print('SMOKE_OK'); print('DONE')"]), flow=flow)
''',
    encoding="utf-8",
)
output = cli("rpk-wave", "work.py", "--headless")
assert "SMOKE_OK" in output and "PASS" in output, output
wave_static = Path(importlib.import_module("rpkbin.wave").__file__).resolve().parent / "static"
for asset in ("index.html", "app.css", "app.js"):
    assert (wave_static / asset).is_file(), wave_static / asset
from rpkbin.wave.web import WaveServer

server = WaveServer()
try:
    url = server.start()
    assets = {"": wave_static / "index.html", "app.css": wave_static / "app.css",
              "app.js": wave_static / "app.js"}
    assets.update({name: wave_static.parent / name for name in
                   ("history.js", "analysis.js", "reading.js", "jobs.js", "flow.js", "icons.js")})
    for route, path in assets.items():
        with urlopen(urljoin(url, route), timeout=5) as response:
            assert response.status == 200
            assert response.read() == path.read_bytes()
finally:
    server.stop()
    server.workspace.close()

isasim_gui_static = Path(importlib.import_module("rpkbin.app.isasim_gui").__file__).resolve().parent / "static"
assert (isasim_gui_static / "index.html").is_file(), isasim_gui_static / "index.html"

if flavor == "base":
    for name in ("brotli", "apytypes", "PySide6", "qtconsole"):
        assert importlib.util.find_spec(name) is None, name
else:
    import brotli
    assert unpack(pack(rows, compress="brotli")) == rows
    for name in ("rpkbin.app.calc.core.session", "rpkbin.app.calc.ui.app"):
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(prefix), (name, module.__file__)
    from rpkbin.app.calc.core import CalcConfig, WorkspaceSession
    session = WorkspaceSession(CalcConfig(), seed_demo_vars=False)
    assert not session.execute("gain = 3\ndef scale(x):\n    return gain * x").error_text
    assert not session.execute("answer = scale(7)").error_text
    assert session.user_ns["answer"] == 21
    assert not session.execute("n = -255\ns = np.float32(1.5)\nf = APyFloat.from_float(1.5, exp_bits=5, man_bits=2, bias=7)").error_text
    assert session.get_inspector_snapshot("n").native_bits["hex"] == "-0xff"
    assert session.get_inspector_snapshot("s").native_bits["hex"] == "0x3FC00000"
    from rpkbin.app.calc.core.editing import EditJournal
    journal = EditJournal(session)
    journal.apply([("f", None)], lambda: session.toggle_target_bit("f", None, 7))
    assert session.get_inspector_snapshot("f").native_bits["hex"] == "0x9E"
    assert session.user_ns["f"].bias == 7
    journal.replay()
    assert session.user_ns["f"].to_bits() == 0x1E
    # Artwork must also load from the installed wheel, outside the source tree.
    from PySide6.QtWidgets import QApplication
    from rpkbin.app.calc.ui.icons import application_icon
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
        if any(name == "rpkbin/job_manager.py" or name.startswith("rpkbin/job_manager/")
               for name in archive.namelist()):
            raise ValueError("Wheel contains the retired package")
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
        env = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        # Keep pip's normal cache (including setup-python's restored cache).
        for flavor, extra in (("base", ""), ("all", "[all]")):
            flavor_dir = root / flavor
            env_dir = flavor_dir / "venv"
            venv.EnvBuilder(with_pip=True).create(env_dir)
            python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            work = flavor_dir / "work"
            work.mkdir()
            home = flavor_dir / "home"
            home.mkdir()
            smoke_env = dict(env, HOME=str(home), USERPROFILE=str(home),
                             RPK_WAVE_ARCHIVE_DIR=str(work / "archives"))
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
            for isolate in (CORE_SMOKE, FLOW_SMOKE):
                subprocess.run(
                    [str(python), "-X", "utf8", "-I", "-c", isolate],
                    cwd=work,
                    env=smoke_env,
                    check=True,
                    timeout=30,
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
