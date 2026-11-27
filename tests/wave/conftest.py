"""Pytest fixtures isolating Wave tests from the user's real archive directory."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_wave_archive_dirs(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("wave_env")
    monkeypatch.setenv("RPK_WAVE_ARCHIVE_DIR", str(root / "archives"))
    monkeypatch.setenv("RPK_WAVE_TEMP_DIR", str(root / "temp"))
