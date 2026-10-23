"""User-owned startup files: preservation, explicit execution and shared globals."""
import json

import pytest

from rpkbin.calc.core import CalcConfig, WorkspaceSession
from rpkbin.calc.core.startup import STARTUP_TEMPLATE, StartupFile, prepare_startup
from rpkbin.calc.core.settings_transfer import export_preferences, parse_preferences


def test_template_is_inert_and_custom_imports_override_packs(tmp_path):
    store = StartupFile(tmp_path / 'settings.json')
    store.initialize()
    assert store.read() == STARTUP_TEMPLATE
    assert all(not line.strip() or line.startswith('#') for line in store.read().splitlines())
    store.path.write_text('from math import prod as fft\nimport math as custom_math\ndef scale(x):\n    return x * gain\n', encoding='utf-8')
    session = WorkspaceSession(CalcConfig(startup_file=str(store.path)), seed_demo_vars=False)
    import math
    assert session.user_ns['fft'] is math.prod
    assert not session.execute('gain = 3\nresult = scale(2)').error_text
    assert session.user_ns['result'] == 6
    assert not {'custom_math', 'fft', 'scale'} & set(session.user_variable_names())
    session.config.auto_import_groups['fft'] = False
    session.reload_auto_imports(run_startup=False)
    assert session.user_ns['fft'] is math.prod


def test_legacy_migration_preserves_conflicts_and_has_one_persistent_source(tmp_path):
    path = tmp_path / 'settings.json'
    legacy = '# 保留\ndef helper():\n    return 7\n'
    cfg = CalcConfig(startup_script=legacy)
    cfg.save(path)
    store = StartupFile(path)
    store.initialize('existing = 2')
    cfg = CalcConfig.load(path)
    _, notice = prepare_startup(cfg, path)
    assert store.read() == 'existing = 2'
    assert notice and list(tmp_path.glob('startup.legacy-*.py'))[0].read_text(encoding='utf-8') == legacy
    assert 'startup_script' not in json.loads(path.read_text())
    assert 'startup_file' not in json.loads(path.read_text())
    assert cfg.startup_file == str(store.path)
    store.path.write_text('edited = 3', encoding='utf-8')
    prepare_startup(cfg, path)
    assert store.read() == 'edited = 3'


def test_migration_without_conflict_preserves_exact_code(tmp_path):
    path = tmp_path / 'settings.json'
    cfg = CalcConfig(startup_script='數值 = 9\n')
    store, notice = prepare_startup(cfg, path)
    assert store.read() == '數值 = 9\n' and not notice
    assert cfg.startup_script == ''


def test_external_save_requires_explicit_reload_and_errors_include_filename(tmp_path):
    store = StartupFile(tmp_path / 'settings.json')
    store.initialize('value = 1')
    session = WorkspaceSession(CalcConfig(startup_file=str(store.path)), seed_demo_vars=False)
    store.path.write_text('value = 2', encoding='utf-8')
    session.reload_auto_imports(run_startup=False)
    assert session.user_ns['value'] == 1
    session.reload_auto_imports()
    assert session.user_ns['value'] == 2
    store.path.write_text('# first line\nraise RuntimeError("broken-startup")\n', encoding='utf-8')
    session.reload_auto_imports()
    assert str(store.path) in session.user_ns['_calc_startup_error']
    assert 'line 2' in session.user_ns['_calc_startup_error']
    assert not session.user_ns['_calc_startup_status']['ok']
    assert not session.execute('1+2').error_text
    store.path.write_text('value = 4', encoding='utf-8')
    session.reload_auto_imports()
    assert '_calc_startup_error' not in session.user_ns
    assert session.user_ns['_calc_startup_status']['ok']


def test_script_import_write_is_guarded_and_keeps_recovery_copy(tmp_path):
    store = StartupFile(tmp_path / 'settings.json')
    store.initialize('original = 1')
    revision = store.revision()
    store.path.write_text('external = 2', encoding='utf-8')
    with pytest.raises(ValueError, match='another editor'):
        store.write('imported = 3', expected_revision=revision)
    assert store.read() == 'external = 2'
    store.write('imported = 3', expected_revision=store.revision())
    assert store.read() == 'imported = 3'
    assert list(tmp_path.glob('startup.before-import-*.py'))[0].read_text() == 'external = 2'


def test_transfer_carries_code_without_local_path_and_validates_terms():
    payload = export_preferences(CalcConfig(startup_script='import scipy', startup_file='local/private.py', keep_english_terms=True))
    assert 'startup_file' not in payload['preferences']
    assert parse_preferences(json.dumps(payload))['startup_script'] == 'import scipy'
    payload['preferences']['keep_english_terms'] = 'yes'
    with pytest.raises(ValueError, match='type'):
        parse_preferences(json.dumps(payload))


def test_external_python_encoding_is_preserved_by_import(tmp_path):
    store = StartupFile(tmp_path / 'settings.json')
    store.initialize('# coding: cp1252\nname = "café"\n')
    assert 'café' in store.read()
    source = '# coding: cp1252\nname = "déjà"\n'
    store.write(source, expected_revision=store.revision())
    assert store.read() == source


@pytest.mark.parametrize('exception', ['KeyboardInterrupt', 'SystemExit'])
def test_startup_control_exceptions_leave_an_observable_usable_session(exception):
    session = WorkspaceSession(CalcConfig(startup_script=f'raise {exception}()'), seed_demo_vars=False)
    assert exception in session.user_ns['_calc_startup_error']
    assert not session.user_ns['_calc_startup_status']['ok']
    assert not session.execute('1+2').error_text


def test_invalid_encoding_never_creates_or_replaces_a_file(tmp_path):
    store = StartupFile(tmp_path / 'settings.json')
    with pytest.raises(ValueError, match='encoding'):
        store.initialize('# coding: unknown_codec\nx = 1')
    assert not store.path.exists()
    store.initialize('x = 1')
    with pytest.raises(ValueError, match='encoding'):
        store.write('# coding: unknown_codec', expected_revision=store.revision())
    assert store.read() == 'x = 1'
