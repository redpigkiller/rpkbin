"""Stream-based interactive debugger, with expression text preserved."""

from __future__ import annotations

import re as _re
import shlex as _shlex
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import TextIO as _TextIO

from .errors import IsaSimError, StateAccessError, TargetError, UsageError
from .events import _dec_or_hex, _format, _hex, _safe_detail_value
from .snapshot import Snapshot

if _TYPE_CHECKING:
    from .sim import Simulator


def _number(text):
    try:
        return int(text, 16 if text.lower().startswith(("0x", "-0x")) else 10)
    except ValueError as error:
        raise UsageError(f"value={text!r}: expected decimal/hex integer") from error


def _where(text):
    try:
        return _number(text)
    except UsageError:
        return text


def _format_eval_value(value: int, *, negative_hex: bool) -> str:
    """Format an evaluated integer/bool with decimal and hex, falling back to hex on wide ints."""
    num = int(value)
    text = _dec_or_hex(num, None)
    if text.startswith(("0x", "-0x")):
        return text
    if num >= 0 or negative_hex:
        return f"{text} (0x{num:x})"
    return text


def run_repl(sim: Simulator, stdin: _TextIO, stdout: _TextIO) -> None:
    aliases = {
        "r": "run",
        "continue": "run",
        "c": "run",
        "s": "step",
        "b": "break",
        "p": "print",
        "q": "quit",
    }
    commands = (
        "help",
        "run",
        "step",
        "until",
        "break",
        "tbreak",
        "watch",
        "delete",
        "info",
        "regs",
        "mem",
        "print",
        "poke",
        "irq",
        "stim",
        "display",
        "undisplay",
        "disasm",
        "trace",
        "snapshot",
        "load",
        "reset",
        "where",
        "source",
        "quit",
    )
    syntax = {
        "help": "help [command]",
        "run": "run [max_steps]",
        "step": "step [n]",
        "until": "until <where>",
        "break": "break <where> [if <expr>]",
        "tbreak": "tbreak <where> [if <expr>]",
        "watch": "watch <target> [r|w|rw] [if <expr>]",
        "delete": "delete <id|all>",
        "info": "info [break]",
        "regs": "regs",
        "mem": "mem <space> <addr> [count]",
        "print": "print <expr>",
        "poke": "poke <target> <value>",
        "irq": "irq <line> <0|1>",
        "stim": "stim read_seq <target> <v,v,...> | stim list | stim clear",
        "display": "display <expr>",
        "undisplay": "undisplay <id>",
        "disasm": "disasm [<where>] [count]",
        "trace": "trace last <n> | trace on <path> [text|jsonl] | trace off",
        "snapshot": "snapshot save <name> | snapshot restore <name> | snapshot list | snapshot export <name> <path> | snapshot import <name> <path>",
        "load": "load hex <path> [big|little] | load bin <path> <base> [big|little] | load sym <path>",
        "reset": "reset",
        "where": "where",
        "source": "source [<where>] [context]",
        "quit": "quit",
    }
    snapshots = {}
    echo = False

    def emit(event):
        if echo and event.kind != "stop":
            stdout.write(_format(event, sim._mcu) + "\n")

    sim.subscribe(emit)

    def displays():
        for number, expr in sim._displays.items():
            try:
                value = sim.evaluate(expr)
                stdout.write(
                    f"{number}: {expr} = {_format_eval_value(value, negative_hex=True)}\n"
                )
            except IsaSimError as error:
                stdout.write(f"error: {error}\n")

    def show_source(where=None, context=2):
        addr = sim.state.pc if where is None else sim._where(where)
        rows = sim.source_context(addr, context=context)
        if not rows:
            stdout.write(f"no source at 0x{addr:x}\n")
            return
        location = sim.source_line(addr)
        stdout.write(f"{location[0]}:{location[1]} pc=0x{addr:x}\n")
        for number, text, current in rows:
            stdout.write(f"{'>' if current else ' '} {number}: {text}\n")
        for start, cells, _, instruction in sim._assembly._entries:
            if instruction and start <= addr < start + len(cells):
                actual = [
                    sim._memory[sim._mcu._program][start + offset]
                    for offset in range(len(cells))
                ]
                stdout.write(
                    "cells: "
                    + " ".join(_hex(cell, sim._assembly._cell_bits) for cell in actual)
                    + "\n"
                )
                break

    def stopped(result):
        stdout.write(f"Stopped: {result.stop_reason}\n")
        stdout.write(f"pc={_hex(result.pc, sim._mcu._registers[sim._mcu._pc]['bits'])}")
        label = sim.label_at(result.pc)
        if label:
            stdout.write(f" <{label[0]}+{label[1]}>")
        if result.detail:
            stdout.write(f" {_safe_detail_value(result.detail)}")
        stdout.write("\n")
        if result.stuck_report is not None:
            pc_bits = sim._mcu._registers[sim._mcu._pc]["bits"]
            pcs = ", ".join(_hex(addr, pc_bits) for addr in result.stuck_report.pcs)
            reads = ", ".join(
                f"{space}[0x{addr:x}]={_dec_or_hex(val, None)}"
                for space, addr, val in result.stuck_report.reads
            )
            stdout.write(f"stuck: pcs=[{pcs}] reads=[{reads}]\n")
        displays()
        if sim._assembly is not None and sim.source_line(result.pc) is not None:
            try:
                show_source(context=0)
            except IsaSimError as error:
                stdout.write(f"source unavailable: {error}\n")

    try:
        while True:
            stdout.write("(sim) ")
            stdout.flush()
            line = stdin.readline()
            if not line:
                return
            stdout.write("\n")
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            command = aliases.get(parts[0], parts[0])
            rest = parts[1] if len(parts) > 1 else ""
            try:
                condition = None
                if command in ("print", "display"):
                    args = [rest]
                elif command in ("break", "tbreak") and rest.startswith(("'", '"')):
                    match = _re.fullmatch(r"([\"'])(.*?)\1(?:\s+if(?:\s+(.*))?)?", rest)
                    if match is None:
                        raise UsageError(f"usage: {syntax[command]}")
                    args = [match[2]]
                    condition = match[3]
                    if rest[match.end(2) + 1 :].strip() == "if":
                        condition = ""
                elif command in ("break", "tbreak", "watch"):
                    match = _re.search(r"(?:^|\s)if(?:\s|$)", rest)
                    if match:
                        condition = rest[match.end() :].strip()
                        rest = rest[: match.start()]
                    args = rest.split()
                else:
                    try:
                        args = _shlex.split(rest)
                    except ValueError as error:
                        raise UsageError(f"arguments={rest!r}: {error}") from error
                if command not in commands:
                    stdout.write(f"error: unknown command: {parts[0]}\n")
                    continue
                if condition == "":
                    raise UsageError(f"usage: {syntax[command]}")
                if command in ("print", "display") and not rest:
                    raise UsageError(f"usage: {syntax[command]}")
                if command == "quit":
                    if args:
                        raise UsageError(f"usage: {syntax[command]}")
                    return
                if command == "help":
                    if len(args) > 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    if not args:
                        alias_text = ", ".join(
                            f"{alias}->{target}" for alias, target in aliases.items()
                        )
                        stdout.write(
                            " ".join(commands) + "\n" + f"aliases: {alias_text}\n"
                        )
                    else:
                        target = aliases.get(args[0], args[0])
                        if target not in commands:
                            raise UsageError(f"usage: {syntax[command]}")
                        stdout.write(syntax[target] + "\n")
                elif command == "run":
                    if len(args) > 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    stopped(
                        sim.run(
                            max_steps=_number(args[0]) if args else 1000000,
                            stuck_window=1000,
                        )
                    )
                elif command == "step":
                    if len(args) > 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    echo = True
                    try:
                        result = sim.step(_number(args[0]) if args else 1)
                    finally:
                        echo = False
                    stopped(result)
                elif command == "until":
                    if len(args) != 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    sim.add_breakpoint(_where(args[0]), temporary=True)
                    stopped(sim.run(max_steps=1000000, stuck_window=1000))
                elif command in ("break", "tbreak"):
                    if len(args) != 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    number = sim.add_breakpoint(
                        _where(args[0]), condition=condition, temporary=command == "tbreak"
                    )
                    stdout.write(f"{number}\n")
                elif command == "watch":
                    if not 1 <= len(args) <= 2 or (
                        len(args) == 2 and args[1] not in ("r", "w", "rw")
                    ):
                        raise UsageError(f"usage: {syntax[command]}")
                    number = sim.add_watchpoint(
                        args[0], kind=args[1] if len(args) > 1 else "w", condition=condition
                    )
                    stdout.write(f"{number}\n")
                elif command == "delete":
                    if len(args) != 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    sim.delete("all" if args[0] == "all" else _number(args[0]))
                elif command == "info":
                    if args == ["break"]:
                        stdout.write("\n".join(sim.list_breakpoints()) + "\n")
                    elif not args:
                        stdout.write(
                            f"PC={sim.state.pc} cycle={sim.cycle} steps={sim.steps_total} program={sim._mcu._program}\n"
                        )
                        for name, meta in sim._mcu._spaces.items():
                            regions = (
                                f" regions={list(meta['regions'])!r}"
                                if meta.get("regions")
                                else ""
                            )
                            stdout.write(
                                f"{name} cell_bits={meta['cell_bits']} size={meta['size']} role={meta['role']}{regions}\n"
                            )
                        stdout.writelines(
                            f"{name} bits={meta['bits']} role={meta['role']}\n"
                            for name, meta in sim._mcu._registers.items()
                        )
                        stdout.writelines(
                            f"{name} -> {target}\n"
                            for name, target in sim._mcu.aliases.items()
                        )
                        stdout.writelines(
                            f"mem_alias {name} -> {info.target} (bits={info.bits})\n"
                            for name, info in sim.mem_aliases.items()
                        )
                        stdout.writelines(
                            f"irq.{name}={value}\n"
                            for name, value in sim._irq.items()
                        )
                    else:
                        raise UsageError(f"usage: {syntax[command]}")
                elif command == "regs":
                    if args:
                        raise UsageError(f"usage: {syntax[command]}")
                    for name, meta in sim._mcu._registers.items():
                        text = f"{name}={_hex(sim.read(name), meta['bits'])}"
                        if meta["fields"]:
                            text += (
                                " ("
                                + " ".join(
                                    f"{field}={_dec_or_hex(sim.read(name + '.' + field), None)}"
                                    for field in meta["fields"]
                                )
                                + ")"
                            )
                        stdout.write(text + "\n")
                    stdout.writelines(
                        f"{name} -> {target}\n" for name, target in sim._mcu.aliases.items()
                    )
                    for name, info in sim.mem_aliases.items():
                        try:
                            val = sim.read(name)
                            rendered = (
                                _dec_or_hex(val, None)
                                if info.bit_hi is not None
                                else _hex(val, info.bits)
                            )
                        except StateAccessError:
                            rendered = "<device>"
                        stdout.write(f"{name} -> {info.target} = {rendered}\n")
                    stdout.writelines(
                        f"irq.{name}={value}\n"
                        for name, value in sim._irq.items()
                    )
                elif command == "mem":
                    if not 2 <= len(args) <= 3:
                        raise UsageError(f"usage: {syntax[command]}")
                    space = args[0]
                    addr = _number(args[1])
                    count = _number(args[2]) if len(args) > 2 else 16
                    if count < 1:
                        raise UsageError(f"count={count}")
                    if space not in sim._mcu._spaces:
                        raise TargetError(f"space={space!r}: unknown space")
                    size = sim._mcu._spaces[space]["size"]
                    if not 0 <= addr < size:
                        raise TargetError(f"{space}[{addr}]: outside size={size}")
                    count = min(count, size - addr)
                    for start in range(0, count, 8):
                        values = []
                        for offset in range(start, min(start + 8, count)):
                            try:
                                values.append(
                                    _dec_or_hex(sim.read(f"{space}[{addr + offset}]"), None)
                                )
                            except StateAccessError:
                                values.append("<device>")
                        stdout.write(
                            f"{space}[0x{addr + start:x}]: " + " ".join(values) + "\n"
                        )
                elif command == "print":
                    value = sim.evaluate(args[0])
                    stdout.write(_format_eval_value(value, negative_hex=False) + "\n")
                elif command == "poke":
                    if len(args) != 2:
                        raise UsageError(f"usage: {syntax[command]}")
                    sim.poke(args[0], _number(args[1]))
                elif command == "irq":
                    if len(args) != 2:
                        raise UsageError(f"usage: {syntax[command]}")
                    level = _number(args[1])
                    if level not in (0, 1):
                        raise UsageError(f"level={level}")
                    sim.poke("irq." + args[0], level)
                elif command == "stim":
                    if len(args) == 3 and args[0] == "read_seq":
                        sim.read_seq(args[1], [_number(v) for v in args[2].split(",")])
                    elif args == ["list"]:
                        stdout.write("\n".join(sim.list_stimuli()) + "\n")
                    elif args == ["clear"]:
                        sim.clear_stimuli()
                    else:
                        raise UsageError(f"usage: {syntax[command]}")
                elif command == "display":
                    sim.add_display(args[0])
                    displays()
                elif command == "undisplay":
                    if len(args) != 1:
                        raise UsageError(f"usage: {syntax[command]}")
                    sim.remove_display(_number(args[0]))
                elif command == "disasm":
                    if len(args) > 2:
                        raise UsageError(f"usage: {syntax[command]}")
                    where = _where(args[0]) if args else sim.state.pc
                    count = _number(args[1]) if len(args) > 1 else 8
                    for addr, text, _ in sim.disassemble(where, count):
                        label = sim.label_at(addr)
                        suffix = f" <{label[0]}+{label[1]}>" if label else ""
                        stdout.write(f"0x{addr:x}: {text}{suffix}\n")
                elif command == "trace":
                    if len(args) == 2 and args[0] == "last":
                        stdout.writelines(
                            _format(event, sim._mcu) + "\n"
                            for event in sim.recent_events(_number(args[1]))
                        )
                    elif len(args) in (2, 3) and args[0] == "on":
                        if len(args) == 3 and args[2] not in ("text", "jsonl"):
                            raise UsageError(f"usage: {syntax[command]}")
                        sim.enable_trace(args[1], fmt=args[2] if len(args) == 3 else "text")
                    elif args == ["off"]:
                        sim.disable_trace()
                    else:
                        raise UsageError(f"usage: {syntax[command]}")
                elif command == "snapshot":
                    if len(args) == 2 and args[0] == "save":
                        snapshots[args[1]] = sim.snapshot()
                    elif len(args) == 2 and args[0] == "restore":
                        if args[1] not in snapshots:
                            raise UsageError(f"snapshot={args[1]!r}: unknown")
                        sim.restore(snapshots[args[1]])
                    elif args == ["list"]:
                        stdout.write("\n".join(snapshots) + "\n")
                    elif len(args) == 3 and args[0] == "export":
                        if args[1] not in snapshots:
                            raise UsageError(f"snapshot={args[1]!r}: unknown")
                        snapshots[args[1]].save(args[2])
                    elif len(args) == 3 and args[0] == "import":
                        snapshots[args[1]] = Snapshot.load(args[2])
                    else:
                        raise UsageError(f"usage: {syntax[command]}")
                elif command == "load":
                    if len(args) in (2, 3) and args[0] == "hex":
                        if len(args) == 3 and args[2] not in ("big", "little"):
                            raise UsageError(f"usage: {syntax[command]}")
                        sim.load_hex(args[1], order=args[2] if len(args) == 3 else None)
                    elif len(args) in (3, 4) and args[0] == "bin":
                        if len(args) == 4 and args[3] not in ("big", "little"):
                            raise UsageError(f"usage: {syntax[command]}")
                        sim.load_bin(
                            args[1],
                            base=_number(args[2]),
                            order=args[3] if len(args) == 4 else None,
                        )
                    elif len(args) == 2 and args[0] == "sym":
                        sim.load_symbols(args[1])
                    else:
                        raise UsageError(f"usage: {syntax[command]}")
                elif command == "reset":
                    if args:
                        raise UsageError(f"usage: {syntax[command]}")
                    sim.reset()
                elif command == "where":
                    if args:
                        raise UsageError(f"usage: {syntax[command]}")
                    source = sim.source_line(sim.state.pc)
                    label = sim.label_at(sim.state.pc)
                    stdout.write(
                        (f"{source[0]}:{source[1]}" if source else "")
                        + (f" <{label[0]}+{label[1]}>" if label else "")
                        + "\n"
                    )
                    if sim._assembly is not None:
                        show_source(context=0)
                elif command == "source":
                    if len(args) > 2:
                        raise UsageError(f"usage: {syntax[command]}")
                    show_source(
                        _where(args[0]) if args else None,
                        _number(args[1]) if len(args) == 2 else 2,
                    )
            except IsaSimError as error:
                stdout.write(f"error: {error}\n")
    finally:
        if emit in sim._subscribers:
            sim._subscribers.remove(emit)
