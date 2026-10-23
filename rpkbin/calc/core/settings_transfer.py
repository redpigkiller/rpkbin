"""Versioned preferences transport shared by Settings export and import.

Settings produces JSON such as ``{"kind": "rpk-calc-preferences", "version": 1,
"preferences": {"theme": "light"}}``; Settings consumes it into a draft only.
Unknown versions, keys and invalid values are rejected before any state changes.
Scripts are data here and require separate consent in the import preview. Window
geometry, splitter sizes, console history and namespace data are never exported.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict

from .config import ALL_WORKSPACE_COLUMNS, CalcConfig, DEFAULT_AUTO_IMPORT_GROUPS
from .auto_imports import parse_imports

KIND = "rpk-calc-preferences"
VERSION = 1
RUNTIME_FIELDS = {"window_geometry", "horizontal_sizes", "vertical_sizes", "inspector_collapsed", "startup_file"}
PREFERENCE_FIELDS = frozenset(asdict(CalcConfig())) - RUNTIME_FIELDS
ENUMS = {
    "format_preset": {"q_format", "matlab", "custom"},
    "default_quant": {"RND", "RND_EVEN", "TRN", "TRN_ZERO"},
    "default_overflow": {"SAT", "WRAP"},
    "inspector_view_mode": {"list", "grid"},
    "inspector_pin_mode": {"real", "hex"},
    "bit_order": {"msb_first", "lsb_first"},
    "numeric_display": {"auto", "scientific", "fixed"},
    "language": {"en_US", "zh_TW"}, "theme": {"dark", "light"},
}
RANGES = {"ui_font_size": (8, 24), "code_font_size": (8, 32),
          "ui_scale": (80, 160), "numeric_precision": (1, 17)}


def validate_preferences(values: object) -> dict:
    """Return an independent, strictly validated preferences mapping."""
    if not isinstance(values, dict) or not set(values) <= PREFERENCE_FIELDS:
        raise ValueError("Invalid or unknown preference fields")
    defaults = asdict(CalcConfig())
    for key, value in values.items():
        if type(value) is not type(defaults[key]):
            raise ValueError(f"Invalid value type: {key}")
        if key in ENUMS and value not in ENUMS[key]:
            raise ValueError(f"Invalid option: {key}")
        if key in RANGES and not RANGES[key][0] <= value <= RANGES[key][1]:
            raise ValueError(f"Value outside allowed range: {key}")
        if key == "auto_import_groups":
            if (set(value) - set(DEFAULT_AUTO_IMPORT_GROUPS)
                    or any(type(item) is not bool for item in value.values())):
                raise ValueError("Invalid function packs")
        if key == "custom_imports":
            parse_imports(value)
        if key in ("visible_columns", "column_order"):
            if (not all(type(item) is str and item in ALL_WORKSPACE_COLUMNS for item in value)
                    or len(value) != len(set(value)) or "name" not in value):
                raise ValueError(f"Invalid columns: {key}")
            if key == "column_order" and set(value) != set(ALL_WORKSPACE_COLUMNS):
                raise ValueError("Column order must contain every column")
        if isinstance(value, str) and len(value) > 1_000_000:
            raise ValueError(f"Preference is too large: {key}")
        if key in ("ui_font_family", "code_font_family") and not value.strip():
            raise ValueError(f"Font family is empty: {key}")
    return copy.deepcopy(values)


def export_preferences(config: CalcConfig) -> dict:
    values = {key: value for key, value in asdict(config).items() if key in PREFERENCE_FIELDS}
    return {"kind": KIND, "version": VERSION, "preferences": validate_preferences(values)}


def parse_preferences(text: str) -> dict:
    if len(text) > 2_000_000:
        raise ValueError("Preferences file is too large")
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid preferences JSON") from error
    if (not isinstance(payload, dict) or set(payload) != {"kind", "version", "preferences"}
            or payload.get("kind") != KIND or type(payload.get("version")) is not int
            or payload["version"] != VERSION):
        raise ValueError("Unsupported preferences file type or version")
    return validate_preferences(payload["preferences"])


def merge_preferences(base: CalcConfig, values: dict, *, include_startup: bool = False,
                      include_imports: bool = False) -> CalcConfig:
    validated = validate_preferences(values)
    result = copy.deepcopy(base)
    for key, value in validated.items():
        if key == "startup_script" and not include_startup:
            continue
        if key == "custom_imports" and not include_imports:
            continue
        if key == "auto_import_groups":
            value = {**result.auto_import_groups, **value}
        setattr(result, key, value)
    return result
