"""Editable import preferences, execution boundaries and namespace ownership."""
import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rpkbin.calc.core.auto_imports import apply_imports, parse_imports
from rpkbin.calc.core.config import CalcConfig, KERNEL_CONFIG_FIELDS
from rpkbin.calc.core.session import WorkspaceSession
from rpkbin.calc.core.settings_transfer import export_preferences, merge_preferences, parse_preferences
from rpkbin.calc.ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("source", [
    "from scipy import *", "from .signal import fft", "import sys as _calc_startup_error",
    "x = 1", "print('no')", "if True:\n import math", "import ???", "import math\x00",
])
def test_import_preferences_accept_only_explicit_absolute_imports(source):
    with pytest.raises(ValueError):
        parse_imports(source)


def test_validation_and_transfer_never_import_packages(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    (tmp_path / "calc_import_probe.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    cfg = CalcConfig(custom_imports="import calc_import_probe as probe")
    cfg.save(tmp_path / "settings.json")
    restored = CalcConfig.load(tmp_path / "settings.json")
    assert restored.custom_imports == cfg.custom_imports
    values = parse_preferences(json.dumps(export_preferences(restored)))
    assert merge_preferences(CalcConfig(), values).custom_imports == ""
    assert merge_preferences(CalcConfig(), values, include_imports=True).custom_imports == cfg.custom_imports
    assert not marker.exists()


def test_packs_custom_imports_then_startup_and_user_binding_preservation():
    cfg = CalcConfig(custom_imports="from math import prod as fft\nimport statistics as stats",
                     startup_script="def scale(x):\n    return fft(x) * gain")
    session = WorkspaceSession(cfg, seed_demo_vars=False)
    session.user_ns["gain"] = 2
    assert session.user_ns["fft"]([2, 3]) == 6
    assert session.user_ns["scale"]([2, 3]) == 12
    assert session.user_ns["stats"].mean([1, 3]) == 2
    assert not {"fft", "stats", "scale"} & set(session.user_variable_names())
    session.user_ns["fft"] = 42
    cfg.custom_imports = "from math import sqrt as fft"
    session.reload_auto_imports(run_startup=False)
    assert session.user_ns["fft"] == 42
    assert "stats" not in session.user_ns
    session.reset()
    assert session.user_ns["fft"](9) == 3


def test_failed_statement_has_no_partial_bindings_and_other_statements_work():
    symbols = {}
    apply_imports("from math import sin, no_such_function\nfrom math import prod", symbols)
    assert "sin" not in symbols
    assert symbols["prod"]([2, 3]) == 6
    assert "Line 1" in symbols["_calc_import_error"]
    assert "ImportError" in symbols["_calc_import_error"]
    apply_imports("import math", symbols)
    assert symbols["_calc_import_error"] == ""


@pytest.mark.parametrize("failure", ["raise SystemExit(1)", "raise KeyboardInterrupt()"])
def test_broken_module_cannot_terminate_session(tmp_path, monkeypatch, failure):
    (tmp_path / "calc_broken_import.py").write_text(failure, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    session = WorkspaceSession(CalcConfig(custom_imports="import calc_broken_import"), seed_demo_vars=False)
    assert "calc_broken_import" in session.user_ns["_calc_import_error"]
    assert session.execute("answer = 1 + 2").error_text == ""
    assert session.user_ns["answer"] == 3


@pytest.mark.parametrize("language", ["en_US", "zh_TW"])
def test_settings_edit_cancel_apply_validation_and_reset(tmp_path, language, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = CalcConfig(language=language)
    before = copy.deepcopy(source)
    view = SettingsDialog(source, config_path=tmp_path / "settings.json")
    try:
        view.custom_imports_edit.setPlainText("from math import prod as product")
        assert view._draft_config().custom_imports == "from math import prod as product"
        assert source == before
        errors = []
        monkeypatch.setattr(view, "_warning", errors.append)
        view.custom_imports_edit.setPlainText("x = 1")
        view._on_save()
        assert errors and view.result_config() == before
        # Reset is also a recovery action for an invalid import draft.
        view._restore_defaults()
        assert view.custom_imports_edit.toPlainText() == ""
        view.custom_imports_edit.setPlainText("from math import prod")
        view._on_save()
        assert view.result_config().custom_imports == "from math import prod"
        assert "custom_imports" in KERNEL_CONFIG_FIELDS
        assert source == before
    finally:
        view.close()
        app.processEvents()


@pytest.mark.parametrize("consent", [False, True])
def test_preference_preview_requires_custom_import_opt_in(tmp_path, consent):
    app = QApplication.instance() or QApplication([])
    view = SettingsDialog(CalcConfig(), config_path=tmp_path / "settings.json")
    def accept():
        preview = QApplication.activeModalWidget()
        assert not preview.include_imports.isChecked()
        preview.include_imports.setChecked(consent)
        preview.accept()
    try:
        QTimer.singleShot(0, accept)
        view._show_import_preview({"custom_imports": "import math as custom_math"})
        assert view._draft_config().custom_imports == ("import math as custom_math" if consent else "")
        assert not (tmp_path / "settings.json").exists()
    finally:
        view.close()
        app.processEvents()
