"""Command helpers for machine declarations."""

import argparse
import json
from pathlib import Path
import sys

from .. import __version__
from .compiler import compile_file,compile_source
from .config import CompileConfig
from .diff import _function_names,_module,_validate_window,run_differential
from .errors import ConfigError,McuccError,SourceError,UnsupportedError
from .formcheck import _check_forms
from .target import _ARGS,load_target_file


def _availability_matrix(target):
    result = {}
    for form in target.forms:
        key = form.op, form.width
        count, smallest = result.get(key, (0, form.bytes))
        result[key] = count + 1, min(smallest, form.bytes)
    return result


class _Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,f"error: {message}\n")


def _parser(prog="python -m rpkbin.mcucc"):
    parser=_Parser(prog=prog)
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("compile","verify"):
        command=commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("--target",required=True)
        command.add_argument("--spill-pool",action="append",required=True)
        command.add_argument("--park-depth", type=int, default=0)
        command.add_argument("--park-policy", choices=("smallest", "stack_first", "slot_first"),
                             default="smallest")
        if name=="compile":
            command.add_argument("--no-comments",action="store_true")
            command.add_argument("-o", "--output", dest="output")
            command.add_argument("--report",action="store_true")
        else:
            command.add_argument("--inputs",type=int,default=120)
            command.add_argument("--seed",type=int,default=0)
            command.add_argument("--window",action="append")
    command=commands.add_parser("lint-target")
    command.add_argument("target")
    command=commands.add_parser("check-forms")
    command.add_argument("target")
    command.add_argument("--seeds",type=int,default=1)
    return parser


def _intervals(declarations, option):
    result={}
    for declaration in declarations:
        try:
            space,interval=declaration.split(":")
            low,high=interval.split("-")
            bounds=tuple(int(v,16) if v.lower().startswith("0x") else int(v,10) for v in (low,high))
        except ValueError as exc:
            raise ConfigError(f"{option} {declaration!r}: expected SPACE:LO-HI") from exc
        if space in result:
            raise ConfigError(f"{option} {space!r}: duplicate declaration {declaration!r}")
        result[space]=bounds
    return result


def _window(declarations):
    if declarations is None:
        raise ConfigError("--window is required")
    ranged = [":" in declaration for declaration in declarations]
    if all(ranged):
        return _intervals(declarations, "--window")
    if len(declarations) != 1:
        raise ConfigError(f"--window {declarations!r}: cannot mix or repeat integer windows")
    try:
        return int(declarations[0])
    except ValueError as exc:
        raise ConfigError(f"--window {declarations[0]!r}: expected an integer or SPACE:LO-HI") from exc


def _run(args):
    if args.command=="verify":
        window = _window(args.window)
        if (isinstance(window, int) and window < 1) or args.inputs < 1:
            raise ConfigError(f"--window {window!r} and --inputs {args.inputs!r} must be positive")
    if args.command=="check-forms" and args.seeds<1:
        raise ConfigError(f"--seeds {args.seeds!r}: expected a positive count")
    target,binding,stubs=load_target_file(args.target)
    if args.command == "verify":
        _validate_window(window, {s.name: s.addr_bits for s in target.spaces if s.kind == "data"})
    if args.command=="lint-target":
        matrix=_availability_matrix(target)
        print("op\twidth\tavailable\tforms\tmin_bytes")
        for op in _ARGS:
            for width in ((0,) if op in ("jmp","br","call","ret") else (8,16)):
                count,smallest=matrix.get((op,width),(0,None))
                print(f"{op}\t{width}\t{bool(count)}\t{count}\t{smallest}")
        return 0
    if args.command=="check-forms":
        if binding is None:
            print("unverified: no SIM")
            return 0
        failures, out_of_space, ret_not_fit = _check_forms(target, binding, tuple(range(args.seeds)))
        for failure in failures:
            print(f"{failure.form_id}: {failure.check}: {failure.detail}")
        print(f"c9 skipped: out_of_space={out_of_space} ret_not_fit={ret_not_fit}")
        if not failures:
            print("verified: forms")
        return int(bool(failures))
    config = CompileConfig(_intervals(args.spill_pool, "spill pool"),
                           not getattr(args, "no_comments", False), park_depth=args.park_depth,
                           park_policy=args.park_policy)
    if args.command=="compile":
        result=compile_file(args.source,target,config)
        if args.output is not None:
            if not args.output:
                raise ConfigError("output path must be nonempty")
            try:
                Path(args.output).write_text(result.asm, encoding="utf-8", newline="\n")
            except OSError as exc:
                raise ConfigError(f"output {args.output!r}: {exc}") from exc
        else:
            sys.stdout.write(result.asm)
        if args.report:
            print(json.dumps(result.report(),ensure_ascii=False),file=sys.stderr)
        return 0
    try:
        source=Path(args.source).read_text(encoding="utf-8")
    except (OSError,UnicodeError) as exc:
        raise SourceError(f"{args.source}:1: cannot read source: {exc}") from exc
    names=_function_names(source)
    if binding is None:
        for name in names:
            try:
                result=compile_source(_module(source,name),target,config,args.source)
                status="unverified: asm" if any(f.verified=="unverified: asm" for f in result.functions.values()) else "unverified: no SIM"
            except UnsupportedError as exc:
                status=f"unsupported: {exc}"
            print(f"{name}: {status}")
        return 0
    report=run_differential(source,target,binding,config,
        stubs=stubs or {},seeds=(args.seed,),inputs_per_function=args.inputs,window=window)
    unverified=dict(report._unverified)
    unsupported=dict(report.unsupported)
    failed={failure[0] for failure in report.failures}
    for name in names:
        if name in unverified:
            status=unverified[name]
        elif name in unsupported:
            status=f"unsupported: {unsupported[name]}"
        elif name in failed:
            status="failed"
        else:
            status="verified"
        print(f"{name}: {status}")
    for failure in report.failures:
        print(json.dumps(failure,ensure_ascii=False))
    return int(bool(report.failures))


def _main(argv=None, *, _prog="python -m rpkbin.mcucc"):
    args=_parser(_prog).parse_args(argv)
    try:
        return _run(args)
    except McuccError as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 2


def main(argv=None):
    """Run the installed command using the same options as the module entry."""
    return _main(argv, _prog="rpk-mcucc")
