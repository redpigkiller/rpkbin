"""Configuration management for rpk-calc (persisted to ~/.rpkbin/calc_settings.json)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path.home() / ".rpkbin" / "calc_settings.json"

ALL_WORKSPACE_COLUMNS: tuple[str, ...] = (
    "name",
    "value",
    "format",
    "hex",
    "s2",
    "uint",
    "c1",
    "bin",
    "wl_info",
    "lsb_range",
    "shape",
    "minmax",
)

DEFAULT_VISIBLE_COLUMNS: list[str] = ["name", "value", "format", "hex"]

DEFAULT_AUTO_IMPORT_GROUPS: dict[str, bool] = {
    "basic_math": True,
    "linalg": True,
    "fitting_comm": True,
    "fft": True,
}

# Only these fields affect the kernel; window/font/panel preferences stay in Qt.
KERNEL_CONFIG_FIELDS = (
    "format_preset", "custom_signed_tpl", "custom_unsigned_tpl", "custom_int_tpl",
    "default_quant", "default_overflow", "auto_import_groups", "custom_imports", "startup_file",
    "visible_columns", "inspector_pin_mode",
)

DEFAULT_STARTUP_SCRIPT: str = ""


@dataclass
class CalcConfig:
    """User preferences for FXP notation, quantization/overflow defaults, auto-imports, and UI state."""

    # Display only. Qm.n includes the sign in m; total bits = m + n.
    # Other presets: "matlab" (sfix16_En12), "custom" (display templates).
    format_preset: str = "q_format"
    custom_signed_tpl: str = "Q{iwl}.{fwl}"
    custom_unsigned_tpl: str = "UQ{iwl}.{fwl}"
    custom_int_tpl: str = "{q_prefix}{wl}.0"

    # GUI conversions/value edits only; native APyTypes keeps its own policies.
    # Quantization: "RND" (TIES_POS), "RND_EVEN" (TIES_EVEN), "TRN" (TO_NEG / floor), "TRN_ZERO" (TO_ZERO)
    default_quant: str = "RND"
    # Overflow: "SAT", "WRAP"
    default_overflow: str = "SAT"

    # Auto-import function packs & custom startup script
    auto_import_groups: dict[str, bool] = field(
        default_factory=lambda: dict(DEFAULT_AUTO_IMPORT_GROUPS)
    )
    custom_imports: str = ""
    startup_script: str = DEFAULT_STARTUP_SCRIPT
    # Runtime-only: resolved by the owner of the settings path. Inline source is
    # accepted for headless callers/legacy migration, never persisted on save.
    startup_file: str = ""

    # Workspace & Inspector preferences
    visible_columns: list[str] = field(
        default_factory=lambda: list(DEFAULT_VISIBLE_COLUMNS)
    )
    column_order: list[str] = field(
        default_factory=lambda: list(ALL_WORKSPACE_COLUMNS)
    )
    inspector_view_mode: str = "list"  # "list" | "grid"
    inspector_pin_mode: str = "real"  # "real" | "hex"
    bit_order: str = "msb_first"  # "msb_first" | "lsb_first"
    # Read-only numeric views only; editable/copy source text remains exact.
    numeric_display: str = "auto"  # "auto" | "scientific" | "fixed"
    numeric_precision: int = 6  # decimal places in scientific/fixed modes

    # Appearance is applied live by Qt, independently of kernel execution.
    language: str = "en_US"
    keep_english_terms: bool = False
    theme: str = "dark"
    ui_font_family: str = "Segoe UI"
    ui_font_size: int = 10  # points
    code_font_family: str = "Cascadia Mono"
    code_font_size: int = 10  # points, shared by console and numeric views
    ui_scale: int = 100  # percent
    inspector_collapsed: bool = False
    window_geometry: str = ""
    horizontal_sizes: list[int] = field(default_factory=list)
    vertical_sizes: list[int] = field(default_factory=list)

    def __post_init__(self):
        if self.format_preset not in ("q_format", "matlab", "custom"):
            self.format_preset = "q_format"
        if self.language not in ("en_US", "zh_TW"):
            self.language = "en_US"
        if type(self.keep_english_terms) is not bool:
            self.keep_english_terms = False
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        if self.numeric_display not in ("auto", "scientific", "fixed"):
            self.numeric_display = "auto"
        for name, default, lower, upper in (
            ("ui_font_size", 10, 8, 24), ("code_font_size", 10, 8, 32),
            ("ui_scale", 100, 80, 160),
            ("numeric_precision", 6, 1, 17),
        ):
            value = getattr(self, name)
            setattr(self, name, min(upper, max(lower, value))
                    if type(value) is int else default)
        for name, default in (("ui_font_family", "Segoe UI"), ("code_font_family", "Cascadia Mono")):
            value = getattr(self, name)
            setattr(self, name, value.strip() if isinstance(value, str) and value.strip() else default)

    @classmethod
    def load(cls, path: Path | str | None = None) -> CalcConfig:
        target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        if not target.exists():
            return cls()
        try:
            raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not isinstance(raw, dict):
            return cls()

        cfg = cls()
        for key in (
            "format_preset",
            "custom_signed_tpl",
            "custom_unsigned_tpl",
            "custom_int_tpl",
            "default_quant",
            "default_overflow",
            "startup_script",
            "custom_imports",
            "inspector_view_mode",
            "inspector_pin_mode",
            "bit_order",
            "language",
            "theme",
            "numeric_display",
            "ui_font_family", "code_font_family", "window_geometry",
        ):
            if isinstance(raw.get(key), str):
                setattr(cfg, key, raw[key])

        for key in ("ui_font_size", "code_font_size", "ui_scale", "numeric_precision"):
            if type(raw.get(key)) is int:
                setattr(cfg, key, raw[key])
        if isinstance(raw.get("inspector_collapsed"), bool):
            cfg.inspector_collapsed = raw["inspector_collapsed"]
        if type(raw.get("keep_english_terms")) is bool:
            cfg.keep_english_terms = raw["keep_english_terms"]
        for key in ("horizontal_sizes", "vertical_sizes"):
            value = raw.get(key)
            if (isinstance(value, list) and len(value) == 2
                    and all(type(size) is int and 0 <= size <= 100_000 for size in value)
                    and sum(value) > 0):
                setattr(cfg, key, value)
        cfg.__post_init__()

        if isinstance(raw.get("auto_import_groups"), dict):
            merged = dict(DEFAULT_AUTO_IMPORT_GROUPS)
            for k, v in raw["auto_import_groups"].items():
                if k in merged and isinstance(v, bool):
                    merged[k] = v
            cfg.auto_import_groups = merged

        if isinstance(raw.get("visible_columns"), list):
            cols = [c for c in raw["visible_columns"] if c in ALL_WORKSPACE_COLUMNS]
            if "name" not in cols:
                cols.insert(0, "name")
            cfg.visible_columns = cols or list(DEFAULT_VISIBLE_COLUMNS)

        if isinstance(raw.get("column_order"), list):
            order = [c for c in raw["column_order"] if c in ALL_WORKSPACE_COLUMNS]
            for c in ALL_WORKSPACE_COLUMNS:
                if c not in order:
                    order.append(c)
            cfg.column_order = order

        return cfg

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                values = asdict(self)
                # Preserve explicit inline source until migrated by a frontend.
                # It is omitted once startup.py becomes the single source.
                values.pop("startup_file")
                if not self.startup_script:
                    values.pop("startup_script")
                json.dump(values, stream, indent=2, ensure_ascii=False)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target
