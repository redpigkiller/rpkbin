"""Versioned, JSON-only DTOs shared by the local kernel and Qt frontend."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path

from .core.format_engine import FxpSpec
from .core.fxp_model import BitCellInfo, InspectorSnapshot, PlainValueSnapshot
from .core.session import MatrixSnapshot, WorkspaceRowSnapshot

PROTOCOL_VERSION = 1
PAGE_ROWS = 32
PAGE_COLS = 16
MAX_COPY_CELLS = 100_000
CONTROL_MIME = "application/vnd.rpk.calc.control+json"


def build_id() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("protocol.py", "kernel_runtime.py", "core/session.py",
                 "core/fxp_model.py", "core/config.py", "core/format_engine.py",
                 "core/math_helpers.py", "core/editing.py", "core/representations.py",
                 "core/startup.py", "core/auto_imports.py"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:16]


def inspector_to_dict(snapshot):
    if snapshot is None:
        return None
    return {"kind": "plain" if isinstance(snapshot, PlainValueSnapshot) else "bits",
            "value": asdict(snapshot)}


def inspector_from_dict(payload):
    if payload is None:
        return None
    data = dict(payload["value"])
    if data["coord"] is not None:
        data["coord"] = tuple(data["coord"])
    if payload["kind"] == "plain":
        return PlainValueSnapshot(**data)
    if payload["kind"] != "bits":
        raise ValueError("Unknown Inspector snapshot kind")
    data["spec"] = FxpSpec(**data["spec"])
    data["bits_msb_to_lsb"] = [BitCellInfo(**bit) for bit in data["bits_msb_to_lsb"]]
    return InspectorSnapshot(**data)


def matrix_from_dict(payload):
    data = dict(payload)
    data["shape"] = tuple(data["shape"])
    data["full_shape"] = tuple(data.get("full_shape", ()))
    data["slice_indices"] = tuple(data.get("slice_indices", ()))
    data["spec"] = FxpSpec(**data["spec"]) if data["spec"] is not None else None
    return MatrixSnapshot(**data)


def rows_from_dict(payload):
    return [WorkspaceRowSnapshot(**row) for row in payload]


def validate_envelope(payload, expected_build: str) -> None:
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Calculator kernel protocol mismatch; restart the application")
    if payload.get("build") != expected_build:
        raise ValueError("Calculator GUI/kernel builds differ; restart the application")
