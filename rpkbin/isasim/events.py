"""Structured events and their exact text presentation."""

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WriteRecord:
    kind: str
    name: str
    addr: int | None
    old: int | None
    new: int
    bits: int
    is_field: bool


@dataclass(frozen=True)
class ReadRecord:
    space: str
    addr: int
    value: int
    bits: int


@dataclass(frozen=True)
class Event:
    kind: str
    seq: int
    cycle: int
    pc: int | None
    cells: tuple[int, ...] | None
    cell_bits: int | None
    pc_bits: int
    name: str | None
    text: str
    writes: tuple[WriteRecord, ...]
    reads: tuple[ReadRecord, ...]
    consumed: int | None
    code: str | None
    detail: dict[str, object] | None


@dataclass
class StuckReport:
    pcs: list[int]
    reads: list[tuple[str, int, int]]


@dataclass
class RunResult:
    stop_reason: str
    steps: int
    instructions: int
    cycles: int
    cycle: int
    pc: int
    detail: dict[str, object]
    exception: BaseException | None
    stuck_report: StuckReport | None


_WIDE_INT_LIMIT = 10**640


def _hex(value, bits):
    return "?" if value is None else f"0x{value:0{(bits + 3) // 4}x}"


def _wide_hex(value, bits=None):
    if value < 0:
        return f"-0x{-value:x}"
    if bits is not None:
        return _hex(value, bits)
    return f"0x{value:x}"


def _dec_or_hex(value, bits):
    if value is None:
        return "?"
    if abs(value) >= _WIDE_INT_LIMIT:
        return _wide_hex(value, bits)
    try:
        return str(value)
    except ValueError:
        return _wide_hex(value, bits)


def _safe_detail_value(value: object) -> object:
    """Replace oversized integers inside stop detail containers with hex strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        text = _dec_or_hex(value, None)
        return text if text.startswith(("0x", "-0x")) else value
    if isinstance(value, list):
        return [_safe_detail_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_safe_detail_value(item) for item in value)
    if isinstance(value, dict):
        return {k: _safe_detail_value(v) for k, v in value.items()}
    return value


def _json_safe_value(value, bits=None):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) >= _WIDE_INT_LIMIT:
            return _wide_hex(value, bits)
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item, bits) for item in value]
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    return value


def _format(event, mcu, fmt="text"):
    if fmt == "jsonl":
        payload = asdict(event)
        payload["pc"] = _json_safe_value(payload["pc"], event.pc_bits)
        payload["cells"] = _json_safe_value(payload["cells"], event.cell_bits)
        for write in payload["writes"]:
            bits = write["bits"]
            write["addr"] = _json_safe_value(write["addr"])
            write["old"] = _json_safe_value(write["old"], bits)
            write["new"] = _json_safe_value(write["new"], bits)
        for read in payload["reads"]:
            bits = read["bits"]
            read["addr"] = _json_safe_value(read["addr"])
            read["value"] = _json_safe_value(read["value"], bits)
        payload["detail"] = _json_safe_value(payload["detail"])
        return json.dumps(payload, separators=(",", ":"))
    if event.kind == "external":
        return f"@ c={event.cycle} EXT {event.text}"
    if event.kind == "warning":
        return f"! c={event.cycle} WARN {event.code}: {event.text}"
    if event.kind == "stop":
        return f"= c={event.cycle} STOP {event.code}" + (
            f": {event.text}" if event.text else ""
        )
    changes = []
    for write in event.writes:
        if write.kind == "mem":
            size = mcu._spaces[write.name]["size"]
            digits = len(format(size - 1, "x"))
            label = f"{write.name}[0x{write.addr:0{digits}x}]"
        else:
            label = write.name
        if write.is_field or write.kind == "irq":
            old = _dec_or_hex(write.old, write.bits)
            new = _dec_or_hex(write.new, write.bits)
        else:
            old, new = _hex(write.old, write.bits), _hex(write.new, write.bits)
        changes.append(f"{label}:{old}->{new}")
    prefix = f"#{event.seq} c={event.cycle} pc={_hex(event.pc, event.pc_bits)} "
    if event.kind == "interrupt":
        prefix += f"INTERRUPT {event.name}"
    else:
        prefix += (
            "["
            + " ".join(_hex(c, event.cell_bits) for c in event.cells)
            + f"] {event.text}"
        )
    prefix += " | " + (" ".join(changes) if changes else "-")
    if event.reads:
        reads = []
        for read in event.reads:
            digits = len(format(mcu._spaces[read.space]["size"] - 1, "x"))
            reads.append(
                f"{read.space}[0x{read.addr:0{digits}x}]={_hex(read.value, read.bits)}"
            )
        prefix += " | rd " + " ".join(reads)
    return prefix + f" | +{event.consumed}"
