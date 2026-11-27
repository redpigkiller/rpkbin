"""Phase 4 regression tests for rpk-calc core, local engine, CLI, transport, and controller."""

from __future__ import annotations

import builtins
from dataclasses import asdict, replace
from fractions import Fraction
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import rpkbin
import rpkbin.app.calc as calc_pkg
from rpkbin.app.calc import cli, kernel_client
from rpkbin.app.calc.controller import CalcController
from rpkbin.app.calc.core import public_fxp, startup as calc_startup
from rpkbin.app.calc.core.config import CalcConfig, KERNEL_CONFIG_FIELDS
from rpkbin.app.calc.core.session import WorkspaceRowSnapshot
from rpkbin.app.calc.kernel_client import KernelTransport
from rpkbin.app.calc.kernel_runtime import CalcKernelRuntime
from rpkbin.app.calc.local_engine import SimpleCalcEngine, display_fraction
from rpkbin.app.calc.protocol import PROTOCOL_VERSION, validate_envelope


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _sample_row(name: str = "x") -> dict:
    return asdict(
        WorkspaceRowSnapshot(
            name=name,
            value_str="1",
            format_str="int",
            hex_str="—",
            s2_str="—",
            uint_str="—",
            c1_str="—",
            bin_str="—",
            wl_info_str="—",
            lsb_range_str="—",
            shape_str="1x1",
            minmax_str="1",
            is_array=False,
            is_fxp=False,
            is_saturated=False,
        )
    )


def test_u8_01_restart_kernel_resets_revision_and_accepts_fresh_bootstrap(qapp, tmp_path, monkeypatch):
    ctrl = CalcController(tmp_path / "settings.json")
    monkeypatch.setattr(ctrl.transport, "start", lambda: None)
    monkeypatch.setattr(ctrl.transport, "restart", lambda: None)

    ctrl._on_reply(
        {"op": "bootstrap"},
        {
            "protocol": PROTOCOL_VERSION,
            "build": ctrl.transport.build,
            "workspace": [_sample_row("old")],
            "selected_var": "old",
            "selected_coord": None,
            "inspector": None,
            "revision": 2,
        },
    )
    assert ctrl.revision == 2

    ctrl.restart_kernel()
    assert ctrl.revision == -1

    ctrl._on_reply(
        {"op": "bootstrap"},
        {
            "protocol": PROTOCOL_VERSION,
            "build": ctrl.transport.build,
            "workspace": [_sample_row("fresh")],
            "selected_var": "fresh",
            "selected_coord": None,
            "inspector": None,
            "revision": 0,
        },
    )
    assert ctrl.revision == 0
    assert [row.name for row in ctrl.rows] == ["fresh"]


@pytest.mark.parametrize("existing", [None, "/custom/site-packages"])
def test_u8_02_kernel_transport_pythonpath_uses_package_parent_without_empty_entry(
    qapp, monkeypatch, existing
):
    captured: list[dict] = []

    class FakeSignal:
        def connect(self, _fn):
            pass

    class FakeClient:
        def __init__(self):
            self.shell_channel = SimpleNamespace(message_received=FakeSignal())
            self.iopub_channel = SimpleNamespace(message_received=FakeSignal())

        def start_channels(self):
            pass

        def stop_channels(self):
            pass

        def execute(self, *_args, **_kwargs):
            return "boot-1"

    class FakeManager:
        has_kernel = False

        def __init__(self, **_kwargs):
            self.kernel_spec = SimpleNamespace(argv=[])

        def start_kernel(self, **kwargs):
            captured.append(kwargs["env"])

        def client(self):
            return FakeClient()

        def is_alive(self):
            return True

    monkeypatch.setattr(kernel_client, "QtKernelManager", FakeManager)
    if existing is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", existing)

    transport = KernelTransport(CalcConfig())
    try:
        transport.start()
    finally:
        transport.shutdown()

    expected_root = str(Path(kernel_client.__file__).resolve().parents[3])
    expected = f"{expected_root}{os.pathsep}{existing}" if existing else expected_root
    assert captured[0]["PYTHONPATH"] == expected
    assert not captured[0]["PYTHONPATH"].endswith(os.pathsep)


def test_u8_03_shift_counts_are_unscaled_for_integer_radices():
    calc = SimpleCalcEngine(CalcConfig())
    calc.set_primary("Q4.4")

    assert calc.evaluate("0xFF << 1", "HEX").raw_uint == 0xFE
    assert calc.evaluate("1 << 1", "DEC").raw_uint == 2
    assert calc.evaluate("0b0001 << 10", "BIN").raw_uint == 4
    assert calc.evaluate("0o1 << 3", "OCT").raw_uint == 8
    assert calc.evaluate("0x01 << (1 + 2)", "HEX").raw_uint == 8
    assert calc.evaluate("0x01 << (2 * 2)", "HEX").raw_uint == 16
    assert calc.evaluate("0x01 << 0x02", "REAL").raw_uint == 4
    assert calc.evaluate("(0x01 << 1) + 1", "HEX").raw_uint == 3

    calc.set_primary("Q12.-4")
    res_hex = calc.evaluate("0x01 << 1", "HEX")
    assert res_hex.raw_uint == 2
    assert res_hex.quantized_value == 32
    assert calc.evaluate("1 << 1", "DEC").raw_uint == 2


def test_u8_04_failed_shift_expression_rolls_back_carry_bit():
    calc = SimpleCalcEngine(CalcConfig())
    calc.set_primary("Q8.0")
    calc.evaluate("0x05", "HEX")
    calc.carry_bit = 0

    with pytest.raises(ValueError, match="Shift count"):
        calc.evaluate("(0x81 << 1) << -1")
    assert calc.carry_bit == 0
    assert calc.raw_uint == 5

    with pytest.raises(ValueError, match="zero"):
        calc.evaluate("(0x81 << 1) / 0")
    assert calc.carry_bit == 0
    assert calc.raw_uint == 5


def test_u8_05_deeply_nested_expressions_do_not_hit_recursion_error():
    calc = SimpleCalcEngine(CalcConfig())

    with pytest.raises(ValueError, match="nested"):
        calc.evaluate("(" * 150 + "1" + ")" * 150)

    assert calc.evaluate("~" * 200 + "1").raw_uint == 1
    assert calc.evaluate("(" * 32 + "1" + ")" * 32).quantized_value == 1


def test_u8_06_display_fraction_handles_over_4300_digits_cleanly():
    calc = SimpleCalcEngine(CalcConfig())

    calc.evaluate("1e999 * 1e999 * 1e999 * 1e999 * 1e999")
    assert calc.display("REAL") == "1E+4995"

    calc.evaluate("1e-999 * 1e-999 * 1e-999 * 1e-999 * 1e-999")
    assert calc.display("REAL") == "1E-4995"

    non_terminating = display_fraction(Fraction(10**5000, 3))
    assert non_terminating.startswith("3.333333333333333E+4999")

    ultra_huge = display_fraction(Fraction(1 << 200_000, 1))
    assert "E+" in ultra_huge


def test_u8_07_set_primary_preserves_exact_real_value():
    calc = SimpleCalcEngine(CalcConfig())
    calc.evaluate("0.1")
    assert calc.exact == Fraction(1, 10)
    assert calc.real == 0

    calc.set_primary("Q4.4")
    assert calc.exact == Fraction(1, 10)
    assert calc.raw_uint == 2


def test_u8_08_u8_09_u8_10_cli_eval_empty_stderr_and_qt_argv(monkeypatch, capsys):
    launched: list[tuple] = []

    def fake_launch(argv=None, *, mode=None, config_path=None):
        launched.append((argv, mode, config_path))
        return 0

    import rpkbin.app.calc.ui.app as ui_app

    monkeypatch.setattr(ui_app, "launch", fake_launch)

    assert cli.main(["--eval", ""]) == 0
    assert launched == []

    assert cli.main(["--eval", "1/0"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ZeroDivisionError" in captured.err

    assert cli.main(["--simple"]) == 0
    assert len(launched) == 1
    assert launched[0][0] == [sys.argv[0]]
    assert "--simple" not in launched[0][0]
    assert launched[0][1] == "simple"


def test_u8_11_transport_timeout_ignores_user_busy_time_and_late_replies(qapp, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(kernel_client.time, "monotonic", lambda: clock[0])

    transport = KernelTransport(CalcConfig())
    transport.ready = True
    transport.manager = SimpleNamespace(is_alive=lambda: True, has_kernel=False)
    transport.client = SimpleNamespace(execute=lambda *_a, **_kw: "req-1", stop_channels=lambda: None)

    replies: list = []
    finished: list = []
    transport.reply_received.connect(lambda req, payload: replies.append((req, payload)))
    transport.user_finished.connect(finished.append)

    req_id = transport.request({"op": "snapshot"})
    assert req_id == "req-1"

    clock[0] = 101.0
    transport.note_user_execution()

    clock[0] = 121.0  # 20s spent running user code
    transport._check_health()
    assert replies == []

    transport._handle_reply(
        {"msg_type": "execute_reply", "parent_header": {"msg_id": "user-1"}, "content": {"status": "ok"}}
    )
    assert not transport.busy
    assert len(finished) == 1

    # Immediately after user finishes, only 1s of non-busy time has elapsed.
    transport._check_health()
    assert replies == []

    # Advance another 15s while idle -> total 16s non-busy > 15s -> times out.
    clock[0] = 136.0
    transport._check_health()
    assert len(replies) == 1
    assert replies[0][1]["transport_error"] is True

    # Late execute_reply for timed-out internal request must not clear active user busy state.
    finished.clear()
    transport.note_user_execution()
    transport._handle_reply(
        {"msg_type": "execute_reply", "parent_header": {"msg_id": "req-1"}, "content": {"status": "ok"}}
    )
    assert transport.busy is True
    assert finished == []


def test_u8_12_pin_mode_does_not_trigger_kernel_config_sync(qapp, tmp_path):
    assert "inspector_pin_mode" not in KERNEL_CONFIG_FIELDS

    ctrl = CalcController(tmp_path / "settings.json")
    ctrl.transport.ready = True
    ctrl.set_pin_mode("hex")
    ctrl.update_config(replace(ctrl.config, language="zh_TW"))
    assert not ctrl._mutations


def test_u8_13_transport_fail_cleans_up_resources_on_start_and_timeout(qapp, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(kernel_client.time, "monotonic", lambda: clock[0])
    created_logs: list = []
    created_dirs: list[Path] = []
    stopped: list[str] = []

    class FakeSignal:
        def connect(self, _fn):
            pass

    class FakeClient:
        def __init__(self, fail_start: bool):
            self._fail_start = fail_start
            self.shell_channel = SimpleNamespace(message_received=FakeSignal())
            self.iopub_channel = SimpleNamespace(message_received=FakeSignal())

        def start_channels(self):
            if self._fail_start:
                raise RuntimeError("channels failed")

        def stop_channels(self):
            stopped.append("channels")

        def execute(self, *_args, **_kwargs):
            return "boot-1"

    fail_flag = [True]

    class FakeManager:
        has_kernel = True

        def __init__(self, **_kwargs):
            self.kernel_spec = SimpleNamespace(argv=[])

        def start_kernel(self, **kwargs):
            created_logs.append(kwargs["stdout"])
            created_dirs.append(Path(kwargs["env"]["IPYTHONDIR"]).parent)

        def client(self):
            return FakeClient(fail_flag[0])

        def shutdown_kernel(self, now=False):
            stopped.append(f"kernel:{now}")

        def is_alive(self):
            return True

    monkeypatch.setattr(kernel_client, "QtKernelManager", FakeManager)

    transport = KernelTransport(CalcConfig())
    failures: list[str] = []
    transport.failed.connect(failures.append)

    transport.start()
    assert failures and "channels failed" in failures[-1]
    assert transport.manager is None
    assert transport.client is None
    assert transport._log is None
    assert created_logs[-1].closed is True
    assert transport._temporary is None
    assert not created_dirs[-1].exists()
    assert "channels" in stopped and "kernel:True" in stopped

    # Retry succeeds in starting, then verify 25s startup timeout also cleans up.
    fail_flag[0] = False
    stopped.clear()
    transport.start()
    assert transport.manager is not None
    clock[0] = 126.0
    transport._check_health()
    assert "timed out" in failures[-1]
    assert transport.manager is None
    assert transport.client is None
    assert transport._log is None
    assert created_logs[-1].closed is True
    assert transport._temporary is None
    assert not created_dirs[-1].exists()


def test_u8_14_update_config_skips_pre_ready_and_out_of_sync_and_disables_interaction(qapp, tmp_path):
    ctrl = CalcController(tmp_path / "settings.json")
    enabled_events: list[bool] = []
    ctrl.interaction_enabled.connect(enabled_events.append)

    # Before transport.start() (manager is None, not ready): updates _kernel_config without queuing.
    ctrl.update_config(replace(ctrl.config, default_quant="TRN"))
    assert ctrl._kernel_config["default_quant"] == "TRN"
    assert not ctrl._mutations
    assert enabled_events == []

    # Startup in flight (manager is not None, not ready): defers until bootstrap completes.
    ctrl.transport.manager = object()
    ctrl.update_config(replace(ctrl.config, default_quant="RND_EVEN"))
    assert not ctrl._mutations
    ctrl.transport.ready = True
    ctrl._on_reply(
        {"op": "bootstrap"},
        {
            "protocol": PROTOCOL_VERSION,
            "build": ctrl.transport.build,
            "workspace": [],
            "selected_var": None,
            "selected_coord": None,
            "inspector": None,
            "revision": 0,
        },
    )
    assert [m["op"] for m in ctrl._mutations] == ["config"]
    assert enabled_events[-1] is False

    # Ready & in-sync: disables interaction and queues config op.
    ctrl._mutations.clear()
    enabled_events.clear()
    ctrl.update_config(replace(ctrl.config, default_overflow="WRAP"))
    assert [m["op"] for m in ctrl._mutations] == ["config"]
    assert enabled_events == [False]

    # Out of sync: skips queuing config op until restart re-syncs _kernel_config.
    ctrl._mutations.clear()
    ctrl._out_of_sync = True
    ctrl.update_config(replace(ctrl.config, default_quant="TRN_ZERO"))
    assert not ctrl._mutations
    ctrl.transport.restart = lambda: None
    ctrl.restart_kernel()
    assert ctrl._kernel_config["default_quant"] == "TRN_ZERO"


def test_u8_15_matrix_page_error_or_none_emits_matrix_page_failed(qapp, tmp_path):
    ctrl = CalcController(tmp_path / "settings.json")
    ctrl.revision = 3
    received: list = []
    failed: list = []
    ctrl.matrix_page_received.connect(lambda page, rev: received.append((page, rev)))
    ctrl.matrix_page_failed.connect(lambda name, row, col, rev: failed.append((name, row, col, rev)))

    req = {"op": "page", "name": "A", "row": 32, "col": 16, "request_id": 1}
    ctrl._pending = req
    ctrl._dirty = False
    ctrl._on_reply(req, {"protocol": PROTOCOL_VERSION, "build": ctrl.transport.build, "page": None, "revision": 3})
    assert received == []
    assert failed == [("A", 32, 16, 3)]
    assert ctrl._dirty is True

    req2 = {"op": "page", "name": "A", "row": 0, "col": 0, "request_id": 2}
    ctrl._pending = req2
    ctrl._on_reply(
        req2,
        {"protocol": PROTOCOL_VERSION, "build": ctrl.transport.build, "error": "ValueError: bad", "revision": 3},
    )
    assert received == []
    assert failed == [("A", 32, 16, 3), ("A", 0, 0, 3)]


def test_u8_16_cli_missing_extras_and_unwritable_home(monkeypatch, capsys):
    real_import = builtins.__import__

    def fail_import(target_suffix: str, missing_pkg: str):
        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith(target_suffix) or name == target_suffix:
                raise ModuleNotFoundError(f"No module named {missing_pkg!r}", name=missing_pkg)
            return real_import(name, globals, locals, fromlist, level)

        return _fake_import

    # --eval reports friendly missing apytypes error with pip install "rpkbin[calc]"
    monkeypatch.setattr(builtins, "__import__", fail_import("core.session", "apytypes"))
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--eval", "1+1"])
    assert exc_info.value.code == 2
    assert 'pip install "rpkbin[calc]"' in capsys.readouterr().err

    # GUI import reports friendly missing apytypes/matplotlib error with pip install "rpkbin[calc]"
    for pkg in ("apytypes", "matplotlib"):
        monkeypatch.setattr(builtins, "__import__", fail_import("ui.app", pkg))
        with pytest.raises(SystemExit) as gui_exc:
            cli.main(["--simple"])
        assert gui_exc.value.code == 2
        assert 'pip install "rpkbin[calc]"' in capsys.readouterr().err

    monkeypatch.setattr(builtins, "__import__", real_import)

    # --eval survives unwritable ~/.rpkbin during startup initialization
    def raise_perm(*_args, **_kwargs):
        raise PermissionError("read-only home")

    monkeypatch.setattr(calc_startup, "prepare_startup", raise_perm)
    assert cli.main(["--eval", "1 + 2"]) == 0
    assert capsys.readouterr().out.strip() == "ans = 3"


def test_u8_17_parse_operand_rejects_sign_after_radix_prefix():
    calc = SimpleCalcEngine(CalcConfig())
    for literal, base in (
        ("0x+F", "HEX"),
        ("0x-F", "HEX"),
        ("0b+1", "BIN"),
        ("0o+7", "OCT"),
        ("0x+F", "REAL"),
    ):
        with pytest.raises(ValueError):
            calc.parse_operand(literal, base)


def test_u8_18_validate_envelope_rejects_bool_protocol():
    with pytest.raises(ValueError, match="protocol"):
        validate_envelope({"protocol": True, "build": "b1"}, "b1")


def test_u8_19_cli_help_descriptions_and_eval_mode_mutual_exclusion(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--simple" in help_text and "Simple" in help_text
    assert "--python" in help_text and "Python" in help_text
    assert "--mode" in help_text and "Select" in help_text

    for argv in (["--eval", "1", "--simple"], ["--eval", "1", "--python"], ["--eval", "1", "--mode", "python"]):
        with pytest.raises(SystemExit) as clash:
            cli.main(argv)
        assert clash.value.code == 2
        capsys.readouterr()


def test_u8_20_bit_mutators_and_copy_matrix_return_bool_and_report_busy(qapp, tmp_path):
    ctrl = CalcController(tmp_path / "settings.json")
    errors: list[str] = []
    ctrl.format_error_occurred.connect(errors.append)

    # Unavailable: returns False and copy_matrix reports busy
    assert ctrl.toggle_target_bit(0) is False
    assert ctrl.invert_target_bits() is False
    assert ctrl.clear_target_bits() is False
    assert ctrl.copy_matrix("A", "real") is False
    assert any("busy" in err.lower() for err in errors)

    # Available with selected_var: returns True
    ctrl.transport.ready = True
    ctrl.selected_var = "x"
    assert ctrl.toggle_target_bit(0) is True
    assert ctrl.invert_target_bits() is True
    assert ctrl.clear_target_bits() is True
    assert ctrl.copy_matrix("A", "real") is True


def test_u8_21_kernel_runtime_clc_and_whos_argument_handling(monkeypatch, capsys):
    monkeypatch.setattr(public_fxp, "_active_config", public_fxp._active_config)

    class FakeEvents:
        def register(self, *_args):
            pass

    class FakeDisplayPub:
        def __init__(self):
            self.items: list = []

        def publish(self, **kwargs):
            self.items.append(kwargs)

    class FakeShell:
        def __init__(self):
            self.user_ns = {}
            self.events = FakeEvents()
            self.display_pub = FakeDisplayPub()
            self.magics = {}

        def register_magic_function(self, fn, _kind, name):
            self.magics[name] = fn

    shell = FakeShell()
    runtime = CalcKernelRuntime(shell, asdict(CalcConfig()))
    runtime.session.user_ns["x"] = 42
    runtime.session.user_ns["y"] = 7

    runtime._clc("")
    assert len(shell.display_pub.items) == 1
    with pytest.raises(ValueError, match="arguments"):
        runtime._clc("extra")

    runtime._whos("")
    all_out = capsys.readouterr().out
    assert "x" in all_out and "y" in all_out

    runtime._whos("x")
    x_out = capsys.readouterr().out
    assert "x" in x_out and "y" not in x_out

    runtime._whos("missing")
    assert "No matching variables" in capsys.readouterr().out

    with pytest.raises(ValueError, match="whos"):
        runtime._whos("--bad")


def test_u8_22_package_dir_lists_lazy_fxp_exports():
    names = dir(calc_pkg)
    assert "fxp" in names
    assert "fxpi" in names


def test_u8_23_cli_version_flag(capsys):
    for flag in ("--version", "-V"):
        with pytest.raises(SystemExit) as exc_info:
            cli.main([flag])
        assert exc_info.value.code == 0
        assert f"rpk-calc {rpkbin.__version__}" in capsys.readouterr().out


def test_u8_24_cli_config_flag_forwards_to_launch_and_eval(monkeypatch, tmp_path, capsys):
    cfg_path = tmp_path / "custom_settings.json"
    CalcConfig(default_quant="TRN").save(cfg_path)

    launched: list[tuple] = []
    import rpkbin.app.calc.ui.app as ui_app

    monkeypatch.setattr(ui_app, "launch", lambda argv=None, *, mode=None, config_path=None: launched.append((argv, mode, config_path)) or 0)
    assert cli.main(["-c", str(cfg_path), "--simple"]) == 0
    assert launched == [([sys.argv[0]], "simple", cfg_path)]

    assert cli.main(["--config", str(cfg_path), "--eval", 'fxp(0.6, "Q4.0")']) == 0
    assert "0  [" in capsys.readouterr().out
