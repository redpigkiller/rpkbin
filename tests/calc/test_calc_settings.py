"""Settings interaction and preference transfer contracts; no kernel required."""
import copy
import json
import os
from dataclasses import asdict

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from rpkbin.calc.core.config import CalcConfig, KERNEL_CONFIG_FIELDS
from rpkbin.calc.core.settings_transfer import (
    KIND, VERSION, export_preferences, merge_preferences, parse_preferences,
)
from rpkbin.calc.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app, tmp_path):
    source = CalcConfig(startup_script='# keep existing code\n定數 = 42')
    view = SettingsDialog(source, config_path=tmp_path / 'custom-settings.json')
    view.show()
    app.processEvents()
    yield view
    view.close()
    app.processEvents()


def test_language_and_blank_startup_defaults_preserve_saved_code(tmp_path):
    assert CalcConfig().startup_script == ''
    cfg = CalcConfig(language='zh_TW', startup_script='# 中文\n數值 = 42')
    cfg.save(tmp_path / 'cfg.json')
    restored = CalcConfig.load(tmp_path / 'cfg.json')
    assert restored.language == 'zh_TW'
    assert restored.startup_script == cfg.startup_script
    assert 'language' not in KERNEL_CONFIG_FIELDS
    assert CalcConfig(language='unsupported').language == 'en_US'


@pytest.mark.parametrize('click_label', [False, True])
@pytest.mark.parametrize('pack', ['basic_math', 'linalg', 'fitting_comm', 'fft'])
def test_pack_indicator_and_label_click_save_identity(dialog, app, pack, click_label):
    dialog.tabs.setCurrentIndex(2)
    app.processEvents()
    checkbox = dialog._group_checks[pack]
    before = checkbox.isChecked()
    # Text follows the indicator; choose a point inside its actual label.
    point = QPoint(32 if click_label else 7, checkbox.height() // 2)
    QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=point)
    assert checkbox.isChecked() != before
    dialog._on_save()
    assert dialog.result_config().auto_import_groups[pack] is not before


def test_startup_preview_is_readonly_and_external_file_is_authoritative(dialog):
    assert dialog.startup_edit.isReadOnly()
    assert not hasattr(dialog, 'example_combo')
    dialog.startup_store.path.write_text('def my_helper(x):\n    return np.log10(x)\n', encoding='utf-8')
    dialog._refresh_startup_preview()
    assert 'my_helper' in dialog.startup_edit.toPlainText()
    assert dialog._draft_config().startup_script == dialog.startup_store.read()


@pytest.mark.parametrize('control_name', ['theme_combo', 'language_combo', 'ui_font_size',
                                         'code_font_size', 'scale_spin', 'ui_font_combo', 'code_font_combo'])
def test_wheel_ignores_fields_and_scrolls_parent(dialog, app, control_name):
    dialog.resize(650, 280)
    dialog.tabs.setCurrentIndex(0)
    control = getattr(dialog, control_name)
    scroll = dialog.tabs.widget(0)
    assert isinstance(scroll, QScrollArea)
    scroll.ensureWidgetVisible(control)
    app.processEvents()
    scrollbar = scroll.verticalScrollBar()
    scrollbar.setValue(0)
    before_scroll = scrollbar.value()
    before_value = control.value() if hasattr(control, 'value') else control.currentIndex()
    position = QPointF(control.rect().center())
    event = QWheelEvent(position, QPointF(control.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(control, event)
    app.processEvents()
    assert (control.value() if hasattr(control, 'value') else control.currentIndex()) == before_value
    # sendEvent targets a widget directly, so test ignored dispatch as well as the
    # parent's normal wheel handler; system mouse dispatch performs this forwarding.
    assert not event.isAccepted() or scrollbar.value() > before_scroll
    if scrollbar.value() == before_scroll:
        QApplication.sendEvent(scroll.viewport(), event)
    assert scrollbar.value() > before_scroll


def test_keyboard_and_spinner_buttons_still_work(dialog):
    before = dialog.ui_font_size.value()
    QTest.keyClick(dialog.ui_font_size, Qt.Key.Key_Up)
    assert dialog.ui_font_size.value() == before + 1


def test_transfer_envelope_excludes_runtime_state_and_roundtrips():
    cfg = CalcConfig(language='zh_TW', startup_script='value = 2', window_geometry='private geometry',
                     horizontal_sizes=[123, 456])
    payload = export_preferences(cfg)
    assert payload['kind'] == KIND and payload['version'] == VERSION
    assert not {'window_geometry', 'horizontal_sizes', 'vertical_sizes', 'inspector_collapsed'} & payload['preferences'].keys()
    values = parse_preferences(json.dumps(payload))
    destination = CalcConfig(window_geometry='keep', horizontal_sizes=[50, 60], startup_script='old')
    merged = merge_preferences(destination, values)
    assert merged.language == 'zh_TW'
    assert merged.startup_script == 'old'
    assert merged.window_geometry == 'keep'
    assert merged.horizontal_sizes == [50, 60]
    assert destination.language == 'en_US'
    assert merge_preferences(destination, values, include_startup=True).startup_script == 'value = 2'


@pytest.mark.parametrize('payload', [
    {}, {'kind': KIND, 'version': 2, 'preferences': {}},
    {'kind': KIND, 'version': True, 'preferences': {}},
    {'kind': 'other-program', 'version': 1, 'preferences': {}},
    {'kind': KIND, 'version': 1, 'preferences': {'theme': 'blue'}},
    {'kind': KIND, 'version': 1, 'preferences': {'ui_font_size': True}},
    {'kind': KIND, 'version': 1, 'preferences': {'ui_font_size': 900}},
    {'kind': KIND, 'version': 1, 'preferences': {'startup_script': ['code']}},
    {'kind': KIND, 'version': 1, 'preferences': {'auto_import_groups': {'fft': 'yes'}}},
    {'kind': KIND, 'version': 1, 'preferences': {'visible_columns': ['name', 'invalid']}},
    {'kind': KIND, 'version': 1, 'preferences': {'window_geometry': 'hidden'}},
])
def test_transfer_rejects_wrong_types_versions_and_values(payload):
    with pytest.raises(ValueError):
        parse_preferences(json.dumps(payload))


def test_invalid_import_never_partially_changes_draft(dialog):
    before = asdict(dialog._draft_config())
    with pytest.raises(ValueError):
        dialog._apply_import({'theme': 'light', 'default_quant': 'INVALID'})
    assert asdict(dialog._draft_config()) == before


def test_import_and_restore_are_draft_only_and_cancel_preserves_source(app, tmp_path):
    original = CalcConfig(theme='light', startup_script='private = 1', horizontal_sizes=[100, 300])
    before = copy.deepcopy(asdict(original))
    view = SettingsDialog(original, config_path=tmp_path / 'actual.json')
    view._apply_import({'theme': 'dark', 'language': 'zh_TW', 'startup_script': 'raise RuntimeError()'})
    assert view._draft_config().theme == 'dark'
    assert view.startup_edit.toPlainText() == 'private = 1'
    view._restore_defaults()
    assert view.startup_edit.toPlainText() == 'private = 1'
    assert view._draft_config().horizontal_sizes == [100, 300]
    view.reject()
    assert asdict(original) == before
    assert not (tmp_path / 'actual.json').exists()
    view.close()


@pytest.mark.parametrize('consent', [False, True])
def test_import_preview_requires_explicit_script_consent(dialog, app, consent, tmp_path):
    marker = tmp_path / 'executed.txt'
    script = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')"
    previous = dialog.startup_edit.toPlainText()
    def accept_preview():
        preview = QApplication.activeModalWidget()
        assert preview is not None
        assert not preview.include_startup.isChecked()
        assert preview.script_preview.toPlainText() == script
        preview.include_startup.setChecked(consent)
        preview.accept()
    QTimer.singleShot(0, accept_preview)
    dialog._show_import_preview({'theme': 'light', 'startup_script': script})
    assert dialog._draft_config().theme == 'light'
    assert dialog.startup_edit.toPlainText() == (script if consent else previous)
    assert not marker.exists()


def test_settings_path_and_language_are_explicit(app, tmp_path):
    path = tmp_path / '別的設定' / 'preferences.json'
    view = SettingsDialog(CalcConfig(language='zh_TW'), config_path=path)
    assert view.windowTitle() == '設定'
    assert view.tabs.tabText(0) == '外觀'
    assert view.tabs.tabText(3) == '偏好管理'
    assert view.path_edit.text() == str(path.resolve())
    assert view.language_combo.currentData() == 'zh_TW'
    copy_text = '\n'.join(label.text() for label in view.findChildren(QLabel))
    assert '正好一半時取偶數' in copy_text
    assert 'Q4.12' in copy_text and 'sfix16_En12' in copy_text
    view.close()


def test_export_uses_current_draft_and_import_previews_before_changes(dialog, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'export.json'
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData('light'))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), 'JSON'))
    dialog._export_preferences()
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload['preferences']['theme'] == 'light'
    assert payload['preferences']['startup_script'] == dialog.startup_edit.toPlainText()
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData('dark'))
    previews = []
    monkeypatch.setattr(dialog, '_show_import_preview', previews.append)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), 'JSON'))
    dialog._import_preferences()
    assert previews[0]['theme'] == 'light'
    assert dialog.theme_combo.currentData() == 'dark'


def test_invalid_file_does_not_open_preview_or_change_draft(dialog, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'invalid.json'
    path.write_text('{"theme": "light"}', encoding='utf-8')
    before = asdict(dialog._draft_config())
    warnings = []
    previews = []
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), 'JSON'))
    monkeypatch.setattr(dialog, '_warning', warnings.append)
    monkeypatch.setattr(dialog, '_show_import_preview', previews.append)
    dialog._import_preferences()
    assert warnings and not previews
    assert asdict(dialog._draft_config()) == before


def test_open_settings_folder_uses_actual_config_path(dialog, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    dialog._open_settings_folder()
    assert opened == [str(dialog._config_path.parent).replace('\\', '/')]


def test_imported_script_apply_saves_without_execution_and_cancel_keeps_file(dialog, tmp_path):
    marker = tmp_path / 'ran.txt'
    script = f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")'
    original = dialog.startup_store.read()
    dialog._apply_import({'startup_script': script}, include_startup=True)
    assert dialog.startup_store.read() == original
    assert not marker.exists()
    dialog._on_save()
    assert dialog.startup_store.read() == script
    assert not marker.exists()
    assert dialog.result_config().startup_script == ''
    assert list(tmp_path.glob('startup.before-import-*.py'))


def test_import_conflict_never_overwrites_external_editor(dialog, monkeypatch):
    dialog._apply_import({'startup_script': 'imported = 1'}, include_startup=True)
    dialog.startup_store.path.write_text('external = 2', encoding='utf-8')
    warnings = []
    monkeypatch.setattr(dialog, '_warning', warnings.append)
    dialog._on_save()
    assert warnings and 'another editor' in warnings[0]
    assert dialog.startup_store.read() == 'external = 2'
    assert dialog.result() != dialog.DialogCode.Accepted


def test_editor_opens_text_editor_instead_of_script_association(dialog, monkeypatch):
    import rpkbin.calc.ui.settings_dialog as module
    if module.sys.platform != 'win32':
        pytest.skip('Windows text editor launch contract')
    commands = []
    monkeypatch.setattr(module.subprocess, 'Popen', lambda command: commands.append(command))
    dialog._open_startup_editor()
    assert commands[0][0].lower().endswith('notepad.exe')
    assert commands[0][1] == str(dialog.startup_store.path)


def test_footer_uses_available_width_and_terms_save(dialog):
    assert dialog.apply_hint.width() > 300
    dialog.english_terms_check.setChecked(True)
    dialog._on_save()
    assert dialog.result_config().keep_english_terms is True


def test_bad_external_encoding_keeps_settings_and_repair_controls_available(app, tmp_path):
    (tmp_path / 'startup.py').write_text('# coding: unknown_codec\nx = 1', encoding='utf-8')
    view = SettingsDialog(CalcConfig(), config_path=tmp_path / 'settings.json')
    assert 'unknown_codec' in view.startup_status.text()
    assert view.open_startup_btn.isEnabled()
    assert view.startup_edit.isReadOnly()
    view.startup_store.path.write_text('fixed = 1', encoding='utf-8')
    view._refresh_startup_preview()
    assert view.startup_edit.toPlainText() == 'fixed = 1'
    view.close()


def test_legacy_conflict_notice_remains_available_in_settings(app, tmp_path):
    from rpkbin.calc.controller import CalcController
    path = tmp_path / 'settings.json'
    CalcConfig(startup_script='legacy = 1').save(path)
    (tmp_path / 'startup.py').write_text('existing = 2', encoding='utf-8')
    controller = CalcController(path)
    view = SettingsDialog(controller.config, controller=controller)
    assert 'startup.legacy-' in view.startup_notice.text()
    assert 'Existing startup.py kept' in view.startup_notice.text()
    view.close()


@pytest.mark.parametrize('language,title,ok,cancel', [
    ('en_US', 'Settings', 'OK', 'Cancel'),
    ('zh_TW', '設定', '確定', '取消'),
])
def test_warning_buttons_and_cancel_follow_selected_ui_language(app, tmp_path, language, title, ok, cancel):
    from PySide6.QtWidgets import QMessageBox
    view = SettingsDialog(CalcConfig(language=language), config_path=tmp_path / 'settings.json')
    assert view.cancel_btn.text() == cancel
    def close_warning():
        warning = QApplication.activeModalWidget()
        assert warning.windowTitle() == title
        button = warning.button(QMessageBox.StandardButton.Ok)
        assert button.text() == ok
        button.click()
    QTimer.singleShot(0, close_warning)
    view._warning('Could not export preferences.')
    expected = ('preserves your startup.py' if language == 'en_US' else '保留你的 startup.py')
    assert expected in view.defaults_btn.toolTip()
    view.close()
