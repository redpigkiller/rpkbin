"""Command-line entry point."""

import argparse as _argparse
import sys as _sys
from pathlib import Path as _Path

from .. import __version__ as _version
from .assembler import Assembly as _Assembly
from .assembler import _assemble_file
from ._preprocess import _define_argument
from .errors import (
    IsaSimError as _IsaSimError,
)
from .errors import (
    LoadError as _LoadError,
)
from .errors import (
    UsageError as _UsageError,
)
from .events import _dec_or_hex, _hex, _safe_detail_value
from .mcu import _load_python
from .mcu import load_mcu as _load_mcu
from .repl import run_repl as _run_repl
from .sim import Simulator as _Simulator


def _integer(text):
    stripped = text.strip()
    neg = stripped.startswith("-")
    body = stripped[1:] if stripped[:1] in ("+", "-") else stripped
    low = body.lower()
    if low.startswith("0x"):
        base = 16
    elif low.startswith("0b"):
        base = 2
    elif low.startswith("0o"):
        base = 8
    else:
        base = 10
    try:
        value = int(body, base)
    except ValueError as error:
        raise _argparse.ArgumentTypeError(
            f"invalid integer value: {text!r}"
        ) from error
    return -value if neg else value


def _assembly_arguments(parser):
    parser.add_argument(
        "-I",
        "--include-dir",
        action="append",
        default=[],
        help="add include search directory",
    )
    parser.add_argument(
        "-D",
        action="append",
        default=[],
        metavar="NAME[=VAL]",
        help="define integer constant (NAME or NAME=VAL)",
    )
    parser.add_argument(
        "--endian",
        choices=("little", "big"),
        help="byte order for multi-byte .dw data",
    )
    parser.add_argument(
        "--case-insensitive",
        action="store_true",
        help="treat labels, constants, and operand names case-insensitively",
    )


def _assembly_options(args, parser):
    defines, seen = {}, set()
    for item in args.D:
        try:
            name, value = _define_argument(item)
        except (SyntaxError, ValueError) as error:
            parser.error(f"-D {item!r}: {error}")
        key = name.casefold() if args.case_insensitive else name
        if key in seen:
            parser.error(f"duplicate -D name {name!r}")
        defines[name] = value
        seen.add(key)
    return dict(
        include_paths=args.include_dir,
        defines=defines,
        endian=args.endian,
        case_sensitive_names=not args.case_insensitive,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the installed command using the same options as the module entry."""
    return _main(argv, _prog="rpk-isasim")


def _main(argv=None, *, _prog="python -m rpkbin.isasim"):
    parser = _argparse.ArgumentParser(
        prog=_prog,
        description="Assemble, run, and interactively debug retargetable MCU programs.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {_version}",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="subcommand to execute",
    )
    assembler = subparsers.add_parser(
        "assemble", help="assemble source into a .isa.json bundle"
    )
    assembler.add_argument("mcu", help="path to MCU definition Python file")
    assembler.add_argument("source", help="path to assembly source file")
    assembler.add_argument("-o", "--output", help="output .isa.json bundle path")
    assembler.add_argument("--listing", help="write assembly listing to path")
    _assembly_arguments(assembler)
    for command in ("run", "repl"):
        sub = subparsers.add_parser(
            command,
            help=(
                "run a program until halt or stop condition"
                if command == "run"
                else "start an interactive debugger session"
            ),
        )
        sub.add_argument("mcu", help="path to MCU definition Python file")
        sub.add_argument(
            "program", nargs="?", help="program file (.asm, .isa.json, hex, or bin)"
        )
        sub.add_argument(
            "--format",
            choices=("hex", "bin", "asm", "image"),
            help="program input format",
        )
        sub.add_argument("--space", help="target memory space for hex/bin loading")
        sub.add_argument(
            "--order",
            choices=("big", "little"),
            help="byte order for multi-byte memory cells",
        )
        sub.add_argument(
            "--base", type=_integer, help="starting cell address for bin loading"
        )
        sub.add_argument("--symbols", help="path to symbols JSON file")
        sub.add_argument("--script", help="path to setup script defining setup(sim)")
        sub.add_argument(
            "--trace-reads",
            action="store_true",
            help="include memory reads in execution events",
        )
        _assembly_arguments(sub)
        if command == "run":
            sub.add_argument(
                "--max-steps",
                type=_integer,
                required=True,
                help="maximum instruction/interrupt steps to execute",
            )
            sub.add_argument(
                "--max-cycles",
                type=_integer,
                help="stop once elapsed cycles reach this count",
            )
            sub.add_argument(
                "--stuck-window",
                type=_integer,
                default=1000,
                help="steps without observable change before stopping as stuck (0 disables)",
            )
            sub.add_argument(
                "--stuck-ignore",
                action="append",
                default=[],
                metavar="NAME",
                help="register or field name to ignore in stuck detection",
            )
            sub.add_argument("--trace", help="write event trace to file")
            sub.add_argument(
                "--trace-format",
                choices=("text", "jsonl"),
                default="text",
                help="trace output format",
            )
    args = parser.parse_args(argv)
    if args.command != "assemble" and args.program and args.format is None:
        if args.program.lower().endswith(".asm"):
            args.format = "asm"
        elif args.program.lower().endswith(".isa.json"):
            args.format = "image"
    if args.command != "assemble" and args.program and args.format is None:
        parser.error("--format required with program")
    if (
        args.command != "assemble"
        and args.program
        and args.format == "bin"
        and args.base is None
    ):
        parser.error("--base required with bin")
    if args.command != "assemble" and args.format in ("asm", "image"):
        if not args.program:
            parser.error("program required with asm/image")
        if any(
            value is not None
            for value in (args.space, args.base, args.order, args.symbols)
        ):
            parser.error(
                "asm/image cannot combine with --space, --base, --order or --symbols"
            )
    if args.command == "assemble" or args.format == "asm":
        options = _assembly_options(args, parser)
    elif args.include_dir or args.D or args.endian is not None or args.case_insensitive:
        parser.error("-I, -D, --endian and --case-insensitive require ASM input")
    else:
        options = {}
    sim = None
    try:
        mcu = _load_mcu(args.mcu)
        if args.command == "assemble":
            output = (
                _Path(args.output)
                if args.output
                else _Path(args.source).with_suffix(".isa.json")
            )
            inputs = {_Path(args.mcu).resolve(), _Path(args.source).resolve()}
            outputs = [output.resolve()]
            if args.listing:
                outputs.append(_Path(args.listing).resolve())
            if any(path in inputs for path in outputs) or len(outputs) != len(
                set(outputs)
            ):
                raise _UsageError(
                    "output/listing paths must differ from source, MCU and each other"
                )
            image = _assemble_file(mcu, args.source, **options)
            root = _Path(image._filename).parent
            included = {
                (root / file).resolve()
                for file in image.sources
                if file != image._filename
            }
            if any(path in included for path in outputs):
                raise _UsageError(
                    "output/listing paths must differ from included sources"
                )
            image.save(output)
            if args.listing:
                try:
                    _Path(args.listing).write_text(
                        image.listing(), encoding="utf-8", newline="\n"
                    )
                except (OSError, UnicodeError) as error:
                    raise _LoadError(f"path={args.listing!r}: {error}") from error
            print(f"Assembled {len(image.cells)} cells: {output}")
            return 0
        sim = _Simulator(mcu, trace_reads=args.trace_reads)
        if args.command == "run" and args.trace:
            sim.enable_trace(args.trace, fmt=args.trace_format)
        if args.program:
            if args.format == "hex":
                sim.load_hex(args.program, space=args.space, order=args.order)
            elif args.format == "bin":
                sim.load_bin(
                    args.program, base=args.base, space=args.space, order=args.order
                )
            elif args.format == "asm":
                sim.load_assembly(_assemble_file(mcu, args.program, **options))
            else:
                sim.load_assembly(_Assembly.load(mcu, args.program))
        if args.symbols:
            sim.load_symbols(args.symbols)
        if args.script:
            try:
                module = _load_python(args.script, "_isasim_setup_")
            except Exception as error:
                raise _LoadError(f"path={args.script!r}: {error}") from error
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise _UsageError(f"path={args.script!r}: setup(sim) required")
            setup(sim)
        if args.command == "repl":
            _run_repl(sim, _sys.stdin, _sys.stdout)
            return 0
        result = sim.run(
            max_steps=args.max_steps,
            max_cycles=args.max_cycles,
            stuck_window=args.stuck_window,
            stuck_ignore=tuple(args.stuck_ignore),
        )
        pc_bits = sim._mcu._registers[sim._mcu._pc]["bits"]
        pc = _hex(result.pc, pc_bits)
        print(
            f"RESULT stop_reason={result.stop_reason} steps={result.steps} instructions={result.instructions} cycles={result.cycles} pc={pc}"
        )
        if result.stop_reason != "halted":
            parts = []
            if result.detail:
                parts.append(f"detail={_safe_detail_value(result.detail)}")
            if result.stuck_report is not None:
                pcs = ", ".join(
                    _hex(addr, pc_bits) for addr in result.stuck_report.pcs
                )
                reads = ", ".join(
                    f"{space}[0x{addr:x}]={_dec_or_hex(val, None)}"
                    for space, addr, val in result.stuck_report.reads
                )
                parts.append(f"stuck_pcs=[{pcs}] stuck_reads=[{reads}]")
            if parts:
                print(
                    f"stop: {result.stop_reason} {' '.join(parts)}",
                    file=_sys.stderr,
                )
        return 0 if result.stop_reason == "halted" else 1
    except _IsaSimError as error:
        print(f"error: {error}", file=_sys.stderr)
        return 2
    finally:
        if sim is not None:
            sim.disable_trace()
