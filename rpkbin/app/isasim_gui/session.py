"""Debugger session manager wrapping rpkbin.isasim."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from typing import Any

from rpkbin.isasim import (
    Assembly,
    DebugError,
    Event,
    IsaSimError,
    MCU,
    MCUDefinitionError,
    RunResult,
    Simulator,
    Snapshot,
    SnapshotError,
    StateAccessError,
    UsageError,
    WriteRecord,
    assemble,
    load_mcu,
)


def relocate_breakpoints(
    old_lines: list[str],
    new_lines: list[str],
    breakpoints: dict[int, str | None],
) -> dict[int, str | None]:
    """Relocate 1-based line breakpoints after source code reloads."""

    def _window(lines: list[str], line: int) -> tuple[str, ...]:
        w = []
        for k in range(-2, 3):
            idx = line - 1 + k
            if idx < 0:
                w.append("<BOF>")
            elif idx >= len(lines):
                w.append("<EOF>")
            else:
                w.append(lines[idx].strip())
        return tuple(w)

    relocated: dict[int, str | None] = {}
    for old_line, cond in breakpoints.items():
        target = _window(old_lines, old_line)
        if 1 <= old_line <= len(new_lines) and _window(new_lines, old_line) == target:
            relocated[old_line] = cond
            continue
        matches = [
            cand
            for cand in range(1, len(new_lines) + 1)
            if _window(new_lines, cand) == target
        ]
        if len(matches) == 1:
            relocated[matches[0]] = cond
    return relocated


def parse_memory_filter_details(
    filter_str: str, max_size: int
) -> tuple[set[int] | None, list[str]]:
    """Parse memory filter string and return ``(addresses_or_none, invalid_tokens)``."""
    filter_str = filter_str.strip()
    if not filter_str:
        return None, []

    def _to_int(text: str) -> int:
        return int(text.strip(), 0)

    addrs: set[int] = set()
    invalid: list[str] = []
    for piece in filter_str.split(","):
        p = piece.strip()
        if not p:
            continue
        try:
            if ":" in p:
                base_s, len_s = p.split(":", 1)
                base = _to_int(base_s)
                length = _to_int(len_s)
                if base < 0 or length <= 0 or base >= max_size:
                    invalid.append(p)
                    continue
                for a in range(base, min(base + length, max_size)):
                    addrs.add(a)
            elif ".." in p:
                start_s, end_s = p.split("..", 1)
                start, end = _to_int(start_s), _to_int(end_s)
                if start < 0 or end < start or start >= max_size:
                    invalid.append(p)
                    continue
                for a in range(start, min(end + 1, max_size)):
                    addrs.add(a)
            elif "-" in p and not p.startswith("-"):
                start_s, end_s = p.split("-", 1)
                start, end = _to_int(start_s), _to_int(end_s)
                if start < 0 or end < start or start >= max_size:
                    invalid.append(p)
                    continue
                for a in range(start, min(end + 1, max_size)):
                    addrs.add(a)
            else:
                a = _to_int(p)
                if 0 <= a < max_size:
                    addrs.add(a)
                else:
                    invalid.append(p)
        except ValueError:
            invalid.append(p)
    return addrs, invalid


def parse_memory_filter(filter_str: str, max_size: int) -> set[int] | None:
    """Parse memory filter string e.g. '0x04, 0x10..0x17, 0x20:4' or '4, 16-23'."""
    return parse_memory_filter_details(filter_str, max_size)[0]


def _source_rows(
    image: Assembly, sources: dict[str, str], pc_digits: int, cell_digits: int
):
    rows = {
        file: [
            {
                "line": number,
                "text": text,
                "addr": None,
                "addr_hex": "",
                "cells_hex": "",
                "is_instr": False,
                "addresses": [],
            }
            for number, text in enumerate(source.splitlines(), 1)
        ]
        for file, source in sources.items()
    }
    for record in image.records():
        row = rows[record.file][record.line - 1]
        # Repeated includes keep their first expansion as the default Set PC target.
        if row["addr"] is None:
            row.update(
                addr=record.addr,
                addr_hex=f"0x{record.addr:0{pc_digits}x}",
                cells_hex=" ".join(f"{cell:0{cell_digits}x}" for cell in record.cells),
                is_instr=record.is_instruction,
            )
        if record.is_instruction:
            row["addresses"].append(record.addr)
    return rows


def _format_hex(val: int, bits: int) -> str:
    return f"0x{val:0{(bits + 3) // 4}x}"


def _format_const_hex(val: int) -> str:
    return f"-0x{-val:x}" if val < 0 else f"0x{val:x}"


def _to_signed(val: int, bits: int) -> int:
    return val if val < (1 << (bits - 1)) else val - (1 << bits)


def _safe_dec_str(val: int) -> str | None:
    try:
        return str(val)
    except ValueError:
        return None


def _json_decimal(val: int | None) -> tuple[int | None, str | None]:
    if val is None:
        return None, None
    dec = _safe_dec_str(val)
    return (None, None) if dec is None else (val, dec)


def _format_summary_int(val: Any) -> str:
    if isinstance(val, int) and not isinstance(val, bool):
        dec = _safe_dec_str(val)
        return dec if dec is not None else _format_const_hex(val)
    return str(val)


def _formatted_int_fields(val: int, bits: int) -> dict[str, Any]:
    raw_json_val, uns_str = _json_decimal(val)
    signed_val, sig_str = _json_decimal(_to_signed(val, bits))
    return {
        "value": raw_json_val,
        "hex": _format_hex(val, bits),
        "unsigned": raw_json_val,
        "unsigned_str": uns_str,
        "signed": signed_val,
        "signed_str": sig_str,
    }


class _Program:
    def __init__(
        self,
        mcu_path: Path,
        path: Path,
        script: Path | None,
        *,
        preloaded: _Program | None = None,
    ) -> None:
        self.script = script
        if preloaded is None:
            self.mcu = load_mcu(str(mcu_path))
            self.is_bundle = path.name.lower().endswith(".isa.json")
            if self.is_bundle:
                self.image = Assembly.load(self.mcu, str(path))
            else:
                text = path.read_text(encoding="utf-8-sig")
                self.image = assemble(self.mcu, text, filename=path.as_posix())
        else:
            self.mcu = preloaded.mcu
            self.is_bundle = preloaded.is_bundle
            self.image = preloaded.image
        self.sim = Simulator(self.mcu, trace_reads=True)
        self.sim.load_assembly(self.image)
        self.events: deque[Event] = deque(maxlen=30)
        self.turn_writes: list[WriteRecord] = []
        self.sim.subscribe(self._on_event)
        self.run_setup(self.script)
        self.events.clear()
        self.turn_writes.clear()
        self.info = self.sim.describe()
        if preloaded is None:
            self.registers = {reg.name: reg for reg in self.info.registers}
            self.spaces = {space.name: space for space in self.info.spaces}
            self.pc = next(reg.name for reg in self.info.registers if reg.is_pc)
            self.pc_digits = (self.registers[self.pc].bits + 3) // 4
            program_space = next(space for space in self.info.spaces if space.is_program)
            self.program_space = program_space.name
            self.aliases = dict(self.info.aliases)
            self.sources = self.image.sources
            self.main = self.image.main_key
            self.rows = _source_rows(
                self.image,
                self.sources,
                self.pc_digits,
                (program_space.cell_bits + 3) // 4,
            )
        else:
            self.registers = preloaded.registers
            self.spaces = preloaded.spaces
            self.pc = preloaded.pc
            self.pc_digits = preloaded.pc_digits
            self.program_space = preloaded.program_space
            self.aliases = preloaded.aliases
            self.sources = preloaded.sources
            self.main = preloaded.main
            self.rows = preloaded.rows

    def run_setup(self, script: Path | None) -> None:
        if script is not None:
            setup = run_path(str(script)).get("setup")
            if not callable(setup):
                raise UsageError(f"path={str(script)!r}: setup(sim) required")
            setup(self.sim)

    def _on_event(self, event: Event) -> None:
        self.events.append(event)
        if event.writes:
            self.turn_writes.extend(event.writes)

    def reset(self) -> None:
        self.sim.reset()
        self.events.clear()
        self.turn_writes.clear()


@dataclass(frozen=True)
class _Breakpoint:
    id: int
    condition: str | None


@dataclass(frozen=True)
class _HistoryEntry:
    snapshot: Snapshot
    events: tuple[Event, ...]
    written_mem: frozenset[tuple[str, int]]


class DebugSession:
    """Interactive debugger session for rpkbin.isasim."""

    def __init__(
        self,
        mcu_path: str | Path,
        asm_path: str | Path,
        script_path: str | Path | None = None,
        max_history: int = 200,
        *,
        call_mnemonics: Iterable[str] = (),
        return_mnemonics: Iterable[str] = (),
    ) -> None:
        if type(max_history) is not int or max_history < 0:
            raise ValueError("max_history must be a non-negative integer")
        self.mcu_path = Path(mcu_path).expanduser().resolve()
        self.asm_path = Path(asm_path).expanduser().resolve()
        self.script_path = Path(script_path).expanduser().resolve() if script_path else None
        self.max_history = max_history
        self._mnemonics = {
            name.casefold(): kind
            for kind, names in (("call", call_mnemonics), ("return", return_mnemonics))
            for name in names
        }
        self._history: deque[_HistoryEntry] = deque(maxlen=max_history)
        self._breakpoints: dict[tuple[str, int], _Breakpoint] = {}
        self._watchpoints: dict[int, tuple[str, str, str | None]] = {}
        self._error: str | None = None
        self._last_stop_reason = "ready"
        self._last_detail: dict[str, Any] = {}
        self._memory_space: str | None = None
        self._memory_filter = ""
        self._hide_uninit = False
        self._filter_cache: tuple[str, int, list[int] | None, list[str]] | None = None
        self._snapshot_error: str | None = None
        self._program = _Program(self.mcu_path, self.asm_path, self.script_path)
        self._changed_regs: dict[str, int] = {}
        self._changed_fields: set[str] = set()
        self._changed_mem: set[tuple[str, int]] = set()
        self._last_mem_aliases: dict[str, tuple[bool, int | None]] = {}
        self._check_snapshot()
        self._last_mem_aliases = self._snapshot_mem_aliases()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({str(self.mcu_path)!r}, "
            f"{str(self.asm_path)!r}, max_history={self.max_history})"
        )

    @property
    def mcu(self) -> MCU:
        return self._program.mcu

    @property
    def sim(self) -> Simulator:
        return self._program.sim

    def _compile_condition(self, condition: str | None, program: _Program):
        if not condition:
            return None
        try:
            return program.sim._compile_condition(condition, allow_shorthand=True)
        except DebugError as error:
            raise DebugError(f"Invalid condition {condition!r}: {error}") from error

    def _probe_snapshot(self, program: _Program) -> str | None:
        if not self.max_history:
            return None
        try:
            program.sim.snapshot()
        except SnapshotError as error:
            return str(error)
        return None

    def _snapshot(self) -> Snapshot | None:
        if not self.max_history or self._snapshot_error is not None:
            return None
        try:
            return self.sim.snapshot()
        except SnapshotError as error:
            self._snapshot_error = str(error)
            self._history.clear()
            return None

    def _check_snapshot(self) -> None:
        self._snapshot_error = self._probe_snapshot(self._program)
        if self._snapshot_error is not None:
            self._history.clear()

    def _remember(self) -> None:
        snapshot = self._snapshot()
        if snapshot is not None:
            self._history.append(snapshot)

    def _declared_kind_lookup(self):
        lookup = getattr(self.sim, "instruction_kind", None)
        if callable(lookup) and self._program.info.has_instruction_kinds:
            return lookup
        return None

    def kind_of(self, pc: int) -> str | None:
        lookup = self._declared_kind_lookup()
        if lookup is not None:
            return lookup(pc)
        if self._mnemonics:
            try:
                decoded = self.sim.disassemble(pc)
            except IsaSimError:
                return None
            if decoded and (tokens := decoded[0][1].split()):
                return self._mnemonics.get(tokens[0].casefold())
        return None

    def _step_depth_reason(self) -> str | None:
        if self._declared_kind_lookup() is not None or self._mnemonics:
            return None
        return "No instruction kinds available. Set --call-mnemonics and --return-mnemonics for this MCU."

    def _result(self, result: RunResult) -> None:
        self._last_stop_reason = result.stop_reason
        self._last_detail = dict(result.detail)

    def _mem_alias_value(
        self, info: Any, ranges: tuple[tuple[int, int], ...], sim: Simulator | None = None
    ) -> tuple[bool, int | None]:
        if not any(r_lo <= info.lo and info.hi <= r_hi for r_lo, r_hi in ranges):
            return False, None
        try:
            return True, (sim or self.sim).read(info.name)
        except StateAccessError:
            return False, None

    def _snapshot_mem_aliases(
        self, program: _Program | None = None
    ) -> dict[str, tuple[bool, int | None]]:
        sim = self.sim if program is None else program.sim
        aliases = sim.mem_aliases
        if not aliases:
            return {}
        ranges_by_space: dict[str, tuple[tuple[int, int], ...]] = {}
        snap: dict[str, tuple[bool, int | None]] = {}
        for name, info in aliases.items():
            ranges = ranges_by_space.get(info.space)
            if ranges is None:
                ranges = sim.initialized_ranges(info.space)
                ranges_by_space[info.space] = ranges
            snap[name] = self._mem_alias_value(info, ranges, sim=sim)
        return snap

    def _capture_reg_values(self) -> dict[str, int]:
        return {name: self.sim.read(name) for name in self._program.registers}

    def _cell_snapshot(
        self,
        space: str,
        addr: int,
        ranges_by_space: dict[str, tuple[tuple[int, int], ...]],
    ) -> tuple[bool, int | None]:
        ranges = ranges_by_space.get(space)
        if ranges is None:
            ranges = tuple(self.sim.initialized_ranges(space))
            ranges_by_space[space] = ranges
        if not any(lo <= addr < hi for lo, hi in ranges):
            return False, None
        try:
            return True, self.sim.state.mem[space][addr]
        except StateAccessError:
            return False, None

    def _compute_reg_diffs(
        self, prev_regs: dict[str, int], *, include_pc: bool
    ) -> tuple[dict[str, int], set[str]]:
        changed_regs: dict[str, int] = {}
        changed_fields: set[str] = set()
        pc_name = self._program.pc
        for name, meta in self._program.registers.items():
            if not include_pc and name == pc_name:
                continue
            old_val = prev_regs[name]
            new_val = self.sim.read(name)
            if old_val != new_val:
                changed_regs[name] = old_val
                for info in meta.fields:
                    mask = (1 << (info.hi - info.lo + 1)) - 1
                    if ((old_val >> info.lo) & mask) != ((new_val >> info.lo) & mask):
                        changed_fields.add(f"{name}.{info.name}")
        return changed_regs, changed_fields

    def _written_mem_from_turn(self) -> set[tuple[str, int]]:
        return {
            (w.name, w.addr)
            for w in self._program.turn_writes
            if w.kind == "mem" and w.addr is not None
        }

    def _clear_diffs(self) -> None:
        self._changed_regs = {}
        self._changed_fields = set()
        self._changed_mem = set()

    def _finish_forward_action(
        self,
        pre_snap: Snapshot | None,
        pre_events: tuple[Event, ...],
        pre_regs: dict[str, int],
        pre_mem_aliases: dict[str, tuple[bool, int | None]],
        pre_pc: int,
        pre_steps: int,
        pre_cycle: int,
        *,
        include_pc: bool,
    ) -> None:
        written_mem = self._written_mem_from_turn()
        progressed = (
            self.sim.steps_total > pre_steps
            or bool(self._program.turn_writes)
            or self.sim.state.pc != pre_pc
            or self.sim.cycle != pre_cycle
        )
        if progressed:
            if pre_snap is not None:
                self._history.append(
                    _HistoryEntry(
                        snapshot=pre_snap,
                        events=pre_events,
                        written_mem=frozenset(written_mem),
                    )
                )
            self._changed_regs, self._changed_fields = self._compute_reg_diffs(
                pre_regs, include_pc=include_pc
            )
            self._changed_mem = written_mem
            self._last_mem_aliases = pre_mem_aliases
        else:
            self._clear_diffs()
            self._last_mem_aliases = self._snapshot_mem_aliases()

    def _advance(self, max_steps: int, depth: int | None = None) -> None:
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        pre_regs = self._capture_reg_values()
        pre_mem_aliases = self._snapshot_mem_aliases()
        pre_pc = self.sim.state.pc
        pre_steps = self.sim.steps_total
        pre_cycle = self.sim.cycle
        pre_events = tuple(self._program.events)
        pre_snap = self._snapshot()
        self._program.turn_writes.clear()
        reached_end = True
        for index in range(max_steps):
            pc = self.sim.state.pc
            try:
                point = self.sim.next_breakpoint()
            except DebugError as error:
                self._error = f"DebugError: {error}"
                self._last_stop_reason = "debug_error"
                self._last_detail = {}
                reached_end = False
                break
            if point is not None:
                # A depth step can resume from the breakpoint it already reported.
                resuming = (
                    depth is not None
                    and index == 0
                    and self._last_stop_reason == "breakpoint"
                    and self._last_detail == {"id": point, "pc": pc}
                )
                if not resuming:
                    self._last_stop_reason = "breakpoint"
                    self._last_detail = {"id": point, "pc": pc}
                    reached_end = False
                    break
            kind = self.kind_of(pc) if depth is not None else None
            try:
                result = self.sim.step()
            except DebugError as error:
                self._error = f"DebugError: {error}"
                self._last_stop_reason = "debug_error"
                self._last_detail = {}
                reached_end = False
                break
            self._result(result)
            if result.stop_reason not in {"step_done", "step_limit"}:
                reached_end = False
                break
            if depth is not None:
                if result.instructions:
                    depth += {"call": 1, "return": -1}.get(kind, 0)
                if depth <= 0:
                    reached_end = False
                    break
        if reached_end:
            self._last_stop_reason = "max_steps"
        self._finish_forward_action(
            pre_snap,
            pre_events,
            pre_regs,
            pre_mem_aliases,
            pre_pc,
            pre_steps,
            pre_cycle,
            include_pc=False,
        )

    def _step_depth(self, initial_depth: int, max_steps: int) -> dict[str, Any]:
        self._error = self._step_depth_reason()
        if self._error is None:
            self._advance(max_steps, depth=initial_depth)
        return self.get_state()

    def step_in(self) -> dict[str, Any]:
        self._error = None
        pre_regs = self._capture_reg_values()
        pre_mem_aliases = self._snapshot_mem_aliases()
        pre_pc = self.sim.state.pc
        pre_steps = self.sim.steps_total
        pre_cycle = self.sim.cycle
        pre_events = tuple(self._program.events)
        pre_snap = self._snapshot()
        self._program.turn_writes.clear()
        try:
            self._result(self.sim.step())
        except DebugError as error:
            self._error = f"DebugError: {error}"
            self._last_stop_reason = "debug_error"
            self._last_detail = {}
            self._finish_forward_action(
                pre_snap,
                pre_events,
                pre_regs,
                pre_mem_aliases,
                pre_pc,
                pre_steps,
                pre_cycle,
                include_pc=False,
            )
            raise
        self._finish_forward_action(
            pre_snap,
            pre_events,
            pre_regs,
            pre_mem_aliases,
            pre_pc,
            pre_steps,
            pre_cycle,
            include_pc=False,
        )
        return self.get_state()

    def step_over(self, max_steps: int = 10000) -> dict[str, Any]:
        return self._step_depth(initial_depth=0, max_steps=max_steps)

    def step_out(self, max_steps: int = 10000) -> dict[str, Any]:
        return self._step_depth(initial_depth=1, max_steps=max_steps)

    def step_back(self) -> dict[str, Any]:
        self._error = None
        if self._history:
            entry = self._history.pop()
            pre_regs = self._capture_reg_values()
            prev_mem_aliases = self._snapshot_mem_aliases()
            pre_ranges: dict[str, tuple[tuple[int, int], ...]] = {}
            pre_cells = {
                key: self._cell_snapshot(*key, pre_ranges)
                for key in entry.written_mem
            }
            try:
                self.sim.restore(entry.snapshot)
            except SnapshotError as error:
                self._snapshot_error = str(error)
                self._history.clear()
            else:
                self._program.events.clear()
                self._program.events.extend(entry.events)
                self._program.turn_writes.clear()
                self._changed_regs, self._changed_fields = self._compute_reg_diffs(
                    pre_regs, include_pc=True
                )
                post_ranges: dict[str, tuple[tuple[int, int], ...]] = {}
                self._changed_mem = {
                    key
                    for key in entry.written_mem
                    if pre_cells[key] != self._cell_snapshot(*key, post_ranges)
                }
                self._last_mem_aliases = prev_mem_aliases
                self._last_stop_reason = "step_back"
                self._last_detail = {}
        return self.get_state()

    def continue_run(self, max_steps: int = 50000) -> dict[str, Any]:
        self._error = None
        pre_regs = self._capture_reg_values()
        pre_mem_aliases = self._snapshot_mem_aliases()
        pre_pc = self.sim.state.pc
        pre_steps = self.sim.steps_total
        pre_cycle = self.sim.cycle
        pre_events = tuple(self._program.events)
        pre_snap = self._snapshot()
        self._program.turn_writes.clear()
        try:
            self._result(self.sim.run(max_steps=max_steps))
        except DebugError as error:
            self._error = f"DebugError: {error}"
            self._last_stop_reason = "debug_error"
            self._last_detail = {}
            self._finish_forward_action(
                pre_snap,
                pre_events,
                pre_regs,
                pre_mem_aliases,
                pre_pc,
                pre_steps,
                pre_cycle,
                include_pc=False,
            )
            raise
        self._finish_forward_action(
            pre_snap,
            pre_events,
            pre_regs,
            pre_mem_aliases,
            pre_pc,
            pre_steps,
            pre_cycle,
            include_pc=False,
        )
        return self.get_state()

    def _row(self, file: str, line: int) -> dict[str, Any]:
        rows = self._program.rows.get(file, ())
        if not 1 <= line <= len(rows) or not rows[line - 1]["is_instr"]:
            raise ValueError(f"No instruction on line {line} of {file!r}")
        return rows[line - 1]

    def run_to_cursor(
        self, file: str, line: int, max_steps: int = 50000
    ) -> dict[str, Any]:
        self._error = None
        file = file or self._program.main
        self._row(file, line)
        bp_id = self.sim.add_breakpoint(f"{file}:{line}", temporary=True)
        try:
            self._advance(max_steps)
        finally:
            self.sim.delete(bp_id)
        return self.get_state()

    def set_pc(self, file: str, line: int) -> dict[str, Any]:
        addr = self._row(file or self._program.main, line)["addr"]
        self._poke(self._program.pc, addr)
        self._last_stop_reason = "set_pc"
        self._last_detail = {"pc": addr}
        return self.get_state()

    def _register_bits(self, target: str) -> int:
        name, _, field = target.partition(".")
        meta = self._program.registers[name]
        if field:
            for info in meta.fields:
                if info.name == field:
                    return info.hi - info.lo + 1
            raise KeyError(target)
        return meta.bits

    def _poke(self, target: str, value: int | str) -> None:
        self._error = None
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"{target}: value {value!r} must be int or str")
        parsed_val = int(value.strip(), 0) if isinstance(value, str) else int(value)
        canonical = self._program.aliases.get(target, target)
        mem_alias = self.sim.mem_aliases.get(canonical)
        if mem_alias is not None:
            bits = mem_alias.bits
        elif "[" in canonical:
            space = canonical.partition("[")[0]
            meta = self._program.spaces.get(space)
            bits = meta.cell_bits if meta else None
        elif canonical.startswith("irq."):
            bits = 1
        else:
            try:
                bits = self._register_bits(canonical)
            except KeyError:
                bits = None  # Simulator.poke reports the invalid target.
        if bits is not None:
            min_val = 0 if bits == 1 else -(1 << (bits - 1))
            if not min_val <= parsed_val < (1 << bits):
                raise ValueError(f"{target} ({bits}-bit): value {value!r} out of range")
        pre_regs = self._capture_reg_values()
        prev_mem_aliases = self._snapshot_mem_aliases()
        pre_events = tuple(self._program.events)
        snapshot = self._snapshot()
        self._program.turn_writes.clear()
        self.sim.poke(target, parsed_val)
        written_mem = self._written_mem_from_turn()
        self._changed_regs, self._changed_fields = self._compute_reg_diffs(
            pre_regs, include_pc=True
        )
        self._changed_mem = written_mem
        self._last_mem_aliases = prev_mem_aliases
        if snapshot is not None:
            self._history.append(
                _HistoryEntry(
                    snapshot=snapshot,
                    events=pre_events,
                    written_mem=frozenset(written_mem),
                )
            )
        self._last_stop_reason = "poke"
        self._last_detail = {"target": target, "value": parsed_val}

    def poke(self, target: str, value: int | str) -> dict[str, Any]:
        self._poke(target, value)
        return self.get_state()

    def _install_breakpoint(
        self, program: _Program, file: str, line: int, condition: str | None
    ):
        plan = self._compile_condition(condition, program)
        bp_id = program.sim._add_breakpoint_plan(
            f"{file}:{line}", condition=condition, plan=plan
        )
        return _Breakpoint(bp_id, condition)

    def toggle_breakpoint(
        self, file: str, line: int, condition: str | None = None
    ) -> dict[str, Any]:
        file = file or self._program.main
        self._row(file, line)
        key = (file, line)
        old = self._breakpoints.get(key)
        if condition is None and old is not None:
            self.sim.delete(old.id)
            del self._breakpoints[key]
        elif condition is None or condition.strip() or old is not None:
            replacement = self._install_breakpoint(
                self._program, file, line, (condition or "").strip() or None
            )
            if old is not None:
                self.sim.delete(old.id)
            self._breakpoints[key] = replacement
        return self.get_state()

    def _install_watchpoint(
        self, program: _Program, target: str, kind: str, condition: str | None
    ) -> int:
        canonical = program.aliases.get(target, target)
        cond = (condition.strip() or None) if isinstance(condition, str) else condition
        plan = self._compile_condition(cond, program)
        return program.sim._add_watchpoint_plan(
            canonical, kind=kind, condition=cond, plan=plan
        )

    def add_watchpoint(
        self, target: str, kind: str = "w", condition: str | None = None
    ) -> dict[str, Any]:
        cond = (condition.strip() or None) if isinstance(condition, str) else condition
        number = self._install_watchpoint(self._program, target, kind, cond)
        self._watchpoints[number] = (target, kind, cond)
        return self.get_state()

    def remove_watchpoint(self, watchpoint_id: int | str) -> dict[str, Any]:
        if isinstance(watchpoint_id, bool):
            raise UsageError(f"watchpoint_id={watchpoint_id!r}: expected int")
        if isinstance(watchpoint_id, int):
            wid = watchpoint_id
        elif isinstance(watchpoint_id, str):
            try:
                wid = int(watchpoint_id.strip(), 10)
            except ValueError as error:
                raise UsageError(
                    f"watchpoint_id={watchpoint_id!r}: expected int"
                ) from error
        else:
            raise UsageError(f"watchpoint_id={watchpoint_id!r}: expected int")
        if wid not in self._watchpoints:
            raise UsageError(f"watchpoint_id={wid!r}: unknown watchpoint")
        try:
            self.sim.delete(wid)
        except DebugError as error:
            raise UsageError(str(error)) from error
        del self._watchpoints[wid]
        return self.get_state()

    def clear_watchpoints(self) -> dict[str, Any]:
        for number in self._watchpoints:
            self.sim.delete(number)
        self._watchpoints.clear()
        return self.get_state()

    def clear_breakpoints(self) -> dict[str, Any]:
        for point in self._breakpoints.values():
            self.sim.delete(point.id)
        self._breakpoints.clear()
        return self.get_state()

    def _activate_program(
        self,
        program: _Program,
        breakpoints: dict[tuple[str, int], _Breakpoint],
        watchpoints: dict[int, tuple[str, str, str | None]],
    ) -> dict[str, Any]:
        snapshot_error = self._probe_snapshot(program)
        last_mem_aliases = self._snapshot_mem_aliases(program)
        prev = (
            self._program,
            self._breakpoints,
            self._watchpoints,
            list(self._history),
            self._filter_cache,
            self._snapshot_error,
            dict(self._changed_regs),
            set(self._changed_fields),
            set(self._changed_mem),
            self._last_mem_aliases,
            self._error,
            self._last_stop_reason,
            self._last_detail,
        )
        try:
            self._program = program
            self._breakpoints = breakpoints
            self._watchpoints = watchpoints
            self._history.clear()
            self._filter_cache = None
            self._snapshot_error = snapshot_error
            self._clear_diffs()
            self._last_mem_aliases = last_mem_aliases
            self._error = None
            self._last_stop_reason = "ready"
            self._last_detail = {}
            return self.get_state()
        except Exception:
            (
                self._program,
                self._breakpoints,
                self._watchpoints,
                prev_history,
                self._filter_cache,
                self._snapshot_error,
                self._changed_regs,
                self._changed_fields,
                self._changed_mem,
                self._last_mem_aliases,
                self._error,
                self._last_stop_reason,
                self._last_detail,
            ) = prev
            self._history.clear()
            self._history.extend(prev_history)
            raise

    def _reinstall_watchpoints(
        self, program: _Program
    ) -> dict[int, tuple[str, str, str | None]]:
        return {
            self._install_watchpoint(program, *point): point
            for point in self._watchpoints.values()
        }

    def reset(self) -> dict[str, Any]:
        if self.script_path is None:
            self._history.clear()
            self._error = None
            self._program.reset()
            self._check_snapshot()
            self._clear_diffs()
            self._last_mem_aliases = self._snapshot_mem_aliases()
            self._last_stop_reason = "ready"
            self._last_detail = {}
            return self.get_state()
        try:
            program = _Program(
                self.mcu_path,
                self.asm_path,
                self.script_path,
                preloaded=self._program,
            )
            breakpoints = {
                (file, line): self._install_breakpoint(
                    program, file, line, point.condition
                )
                for (file, line), point in self._breakpoints.items()
            }
            watchpoints = self._reinstall_watchpoints(program)
            return self._activate_program(program, breakpoints, watchpoints)
        except MCUDefinitionError as error:
            self._error = f"MCUDefinitionError: {error}"
            return self.get_state()
        except Exception as error:
            self._error = str(error)
            return self.get_state()

    def reload(self) -> dict[str, Any]:
        try:
            program = _Program(self.mcu_path, self.asm_path, self.script_path)
            breakpoints = {}
            by_file: dict[str, dict[int, str | None]] = {}
            for (file, line), point in self._breakpoints.items():
                by_file.setdefault(file, {})[line] = point.condition
            for file, points in by_file.items():
                if file not in program.sources:
                    continue
                relocated = relocate_breakpoints(
                    self._program.sources[file].splitlines(),
                    program.sources[file].splitlines(),
                    points,
                )
                for line, condition in relocated.items():
                    if program.rows[file][line - 1]["is_instr"]:
                        breakpoints[file, line] = self._install_breakpoint(
                            program, file, line, condition
                        )
            watchpoints = self._reinstall_watchpoints(program)
            return self._activate_program(program, breakpoints, watchpoints)
        except MCUDefinitionError as error:
            self._error = f"MCUDefinitionError: {error}"
            return self.get_state()
        except Exception as error:
            self._error = str(error)
            return self.get_state()

    def set_memory_view(
        self,
        memory_space: str | None = None,
        memory_filter: str | None = None,
        hide_uninit: bool | None = None,
    ) -> None:
        if memory_space is not None:
            self._memory_space = memory_space
        if memory_filter is not None:
            self._memory_filter = memory_filter
        if hide_uninit is not None:
            if not isinstance(hide_uninit, bool):
                raise ValueError("hide_uninit must be a boolean")
            self._hide_uninit = hide_uninit

    def view_state(
        self,
        memory_space: str | None = None,
        memory_filter: str | None = None,
        hide_uninit: bool | None = None,
    ) -> dict[str, Any]:
        saved = (
            self._memory_space,
            self._memory_filter,
            self._hide_uninit,
            self._filter_cache,
        )
        try:
            return self.get_state(
                memory_space=memory_space,
                memory_filter=memory_filter,
                hide_uninit=hide_uninit,
            )
        finally:
            (
                self._memory_space,
                self._memory_filter,
                self._hide_uninit,
                self._filter_cache,
            ) = saved

    def get_state(
        self,
        memory_space: str | None = None,
        memory_filter: str | None = None,
        hide_uninit: bool | None = None,
    ) -> dict[str, Any]:
        self.set_memory_view(memory_space, memory_filter, hide_uninit)
        pc = self.sim.state.pc
        program = self._program
        pc_digits = program.pc_digits
        reg_meta = program.registers
        registers_now = {name: self.sim.read(name) for name in reg_meta}
        names_by_target: dict[str, list[str]] = {}
        for alias, target in program.aliases.items():
            names_by_target.setdefault(target, []).append(alias)
        pc_hex = f"0x{pc:0{pc_digits}x}"

        src_info = self.sim.source_line(pc)
        active_file, active_line = src_info if src_info else (program.main, None)

        detail = ""
        if self._last_stop_reason == "fault":
            c, m = (
                self._last_detail.get("code", ""),
                self._last_detail.get("message", ""),
            )
            detail = f"{c}: {m}" if m else str(c)
        elif self._last_stop_reason == "uninitialized_read":
            sp = self._last_detail.get("space", "mem")
            ad = self._last_detail.get("addr", 0)
            detail = f"uninitialized_read at {sp}[0x{ad:02x}]"
        elif "reason" in self._last_detail:
            detail = str(self._last_detail["reason"])
        elif self._last_detail:
            values = self._last_detail.copy()
            if self._last_stop_reason == "watchpoint" and (
                point := self._watchpoints.get(values["id"])
            ):
                values["target"] = point[0]
            detail = ", ".join(
                f"{k}={_format_summary_int(v)}" for k, v in values.items()
            )

        # Files and lines (built from precomputed static templates)
        files_data: dict[str, list[dict[str, Any]]] = {}
        for fname, static_rows in program.rows.items():
            rows = []
            for srow in static_rows:
                line_idx = srow["line"]
                key = (fname, line_idx)
                point = self._breakpoints.get(key)
                bp_cond = (point.condition or "") if point is not None else None
                is_pc = pc in srow["addresses"]
                rows.append(
                    {
                        **srow,
                        "bp_cond": bp_cond,
                        "is_pc": is_pc,
                    }
                )
            files_data[fname] = rows

        # Registers
        changed_regs = self._changed_regs
        changed_fields = self._changed_fields
        mem_writes = self._changed_mem

        registers: list[dict[str, Any]] = []
        for name, meta in reg_meta.items():
            val = registers_now[name]
            bits = meta.bits
            changed = name in changed_regs
            old_hex = None
            if changed:
                old_val = changed_regs[name]
                old_hex = (
                    _format_hex(old_val, bits) if old_val is not None else None
                )

            fields = []
            for info in meta.fields:
                fn, high, low = info.name, info.hi, info.lo
                fbits = high - low + 1
                fval = self.sim.read(f"{name}.{fn}")
                fval_json, fval_str = _json_decimal(fval)
                fields.append(
                    {
                        "name": fn,
                        "target": f"{name}.{fn}",
                        "bits": fbits,
                        "value": fval_json,
                        "value_str": fval_str,
                        "hex": _format_hex(fval, fbits),
                        "changed": f"{name}.{fn}" in changed_fields,
                    }
                )

            registers.append(
                {
                    "name": name,
                    "bits": bits,
                    "is_flag": meta.role == "flag",
                    **_formatted_int_fields(val, bits),
                    "changed": changed,
                    "old_hex": old_hex,
                    "fields": fields,
                }
            )

        # Memory
        spaces = list(program.spaces.keys())
        if self._memory_space and self._memory_space in program.spaces:
            cur_space = self._memory_space
        else:
            cands = [
                s
                for s, m in program.spaces.items()
                if s != program.program_space and m.access == "rw"
            ]
            cur_space = cands[0] if cands else program.program_space
            self._memory_space = cur_space

        smeta = program.spaces[cur_space]
        s_size = smeta.size
        s_cell_bits = smeta.cell_bits
        s_addr_digits = len(format(s_size - 1, "x"))

        if (
            self._filter_cache is not None
            and self._filter_cache[0] == self._memory_filter
            and self._filter_cache[1] == s_size
        ):
            cached_addrs = self._filter_cache[2]
            filter_errors = self._filter_cache[3]
        else:
            allowed, filter_errors = parse_memory_filter_details(
                self._memory_filter, s_size
            )
            cached_addrs = sorted(allowed) if allowed is not None else None
            self._filter_cache = (
                self._memory_filter,
                s_size,
                cached_addrs,
                filter_errors,
            )

        target_addrs = cached_addrs if cached_addrs is not None else range(s_size)

        cells = []
        cur_ranges = tuple(self.sim.initialized_ranges(cur_space))
        ranges = iter(cur_ranges)
        end_of_space = (s_size, s_size)
        lo, hi = next(ranges, end_of_space)
        # Both the displayed addresses and the simulator ranges are ordered.
        for a in target_addrs:
            while a >= hi:
                lo, hi = next(ranges, end_of_space)
            init = lo <= a
            if self._hide_uninit and not init:
                continue
            try:
                val = self.sim.state.mem[cur_space][a]
            except StateAccessError:
                val = None  # Public peek is unavailable for device-owned cells.
            if init and val is not None:
                cell_fields = _formatted_int_fields(val, s_cell_bits)
            else:
                cell_fields = {
                    "value": _json_decimal(val)[0] if val is not None else None,
                    "hex": "--",
                    "unsigned": None,
                    "unsigned_str": None,
                    "signed": None,
                    "signed_str": None,
                }
            cells.append(
                {
                    "addr": a,
                    "addr_hex": f"0x{a:0{s_addr_digits}x}",
                    "initialized": init,
                    **cell_fields,
                    "changed": (cur_space, a) in mem_writes,
                }
            )

        # Register aliases
        aliases_data = []
        for alias, target in program.aliases.items():
            abits = self._register_bits(target)
            aval = self.sim.read(alias)
            aliases_data.append(
                {
                    "name": alias,
                    "target": target,
                    "bits": abits,
                    **_formatted_int_fields(aval, abits),
                }
            )

        # Memory aliases
        mem_aliases_data = []
        sim_mem_aliases = self.sim.mem_aliases
        if sim_mem_aliases:
            ranges_by_space: dict[str, tuple[tuple[int, int], ...]] = {
                cur_space: cur_ranges
            }
            for name, info in sim_mem_aliases.items():
                space_ranges = ranges_by_space.get(info.space)
                if space_ranges is None:
                    space_ranges = tuple(self.sim.initialized_ranges(info.space))
                    ranges_by_space[info.space] = space_ranges
                init, val = self._mem_alias_value(info, space_ranges)
                prev = self._last_mem_aliases.get(name)
                changed = prev is not None and prev != (init, val)
                if init and val is not None:
                    ma_fields = _formatted_int_fields(val, info.bits)
                else:
                    ma_fields = {
                        "value": None,
                        "hex": "--",
                        "unsigned": None,
                        "unsigned_str": None,
                        "signed": None,
                        "signed_str": None,
                    }
                mem_aliases_data.append(
                    {
                        "name": info.name,
                        "target": info.target,
                        "space": info.space,
                        "lo": info.lo,
                        "hi": info.hi,
                        "endian": info.endian,
                        "bit_order": info.bit_order,
                        "bits": info.bits,
                        "initialized": init,
                        **ma_fields,
                        "changed": changed,
                    }
                )

        constants_data: dict[str, int | None] = {}
        constants_display: dict[str, dict[str, str | None]] = {}
        for cname, cval in program.image.constants.items():
            json_cval, dec_str = _json_decimal(cval)
            constants_data[cname] = json_cval
            constants_display[cname] = {
                "hex": _format_const_hex(cval),
                "unsigned_str": dec_str if cval >= 0 else None,
                "signed_str": dec_str,
            }

        # Trace (last 30 events, newest first)
        trace = []
        for ev in reversed(program.events):
            changes = []
            for w in ev.writes:
                old_txt = _format_summary_int(w.old)
                new_txt = _format_summary_int(w.new)
                if w.kind == "mem":
                    changes.append(f"{w.name}[0x{w.addr:x}]:{old_txt}->{new_txt}")
                else:
                    aliases = names_by_target.get(w.name)
                    label = f"{'/'.join(aliases)} ({w.name})" if aliases else w.name
                    changes.append(f"{label}:{old_txt}->{new_txt}")
            ev_loc = self.sim.source_line(ev.pc) if ev.pc is not None else None
            trace.append(
                {
                    "seq": ev.seq,
                    "cycle": ev.cycle,
                    "pc": ev.pc,
                    "pc_hex": f"0x{ev.pc:0{pc_digits}x}" if ev.pc is not None else "",
                    "file": ev_loc[0] if ev_loc else None,
                    "line": ev_loc[1] if ev_loc else None,
                    "text": ev.text,
                    "changes": ", ".join(changes),
                    "kind": ev.kind,
                }
            )

        return {
            "status": {
                "stop_reason": self._last_stop_reason,
                "error": self._error,
                "detail": detail,
                "cycle": self.sim.cycle,
                "steps": self.sim.steps_total,
                "instructions": self.sim.instructions_total,
                "pc": pc,
                "pc_hex": pc_hex,
                "can_step_back": bool(self._history),
                "step_back_reason": self._snapshot_error
                or ("Snapshot history is disabled." if not self.max_history else None),
                "can_step_depth": self._step_depth_reason() is None,
                "step_depth_reason": self._step_depth_reason(),
                "active_file": active_file,
                "active_file_path": active_file
                if program.is_bundle
                else str(self.asm_path.parent / active_file),
                "program_path": str(self.asm_path),
                "active_line": active_line,
            },
            "files": files_data,
            "registers": registers,
            "aliases": aliases_data,
            "mem_aliases": mem_aliases_data,
            "constants": constants_data,
            "constants_display": constants_display,
            "watchpoints": [
                {"id": number, "target": target, "kind": kind, "condition": condition}
                for number, (target, kind, condition) in self._watchpoints.items()
            ],
            "memory": {
                "spaces": spaces,
                "current_space": cur_space,
                "cells": cells,
                "filter_errors": list(filter_errors),
            },
            "trace": trace,
        }
