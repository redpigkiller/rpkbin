"""CLI entry point for rpk-calc."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from ... import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rpk-calc",
        description="rpk-calc: Engineering & APyTypes Fixed-Point (FXP) interactive calculator.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to settings JSON file",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "-e",
        "--eval",
        dest="eval_expr",
        help="Evaluate a Python/FXP expression headlessly and print the result",
    )
    modes.add_argument(
        "--simple",
        dest="mode",
        action="store_const",
        const="simple",
        help="Launch in Simple standalone fixed-point calculator mode",
    )
    modes.add_argument(
        "--python",
        "--studio",
        dest="mode",
        action="store_const",
        const="python",
        help="Launch in Python Studio workspace mode",
    )
    modes.add_argument(
        "--mode",
        choices=("simple", "python"),
        help="Select launch mode ('simple' or 'python')",
    )
    args = parser.parse_args(argv)

    if args.eval_expr is not None:
        try:
            from .core import config as calc_config
            from .core import public_fxp
            from .core.session import WorkspaceSession
            from .core.startup import prepare_startup
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.split(".")[0] in {"apytypes", "matplotlib"}:
                parser.error('Calculator dependencies are missing; install with: pip install "rpkbin[calc]"')
            raise

        cfg = calc_config.CalcConfig.load(args.config)
        target_cfg_path = args.config if args.config is not None else calc_config.DEFAULT_CONFIG_PATH
        try:
            prepare_startup(cfg, target_cfg_path)
        except (OSError, ValueError, UnicodeError):
            pass
        previous_provider = public_fxp._active_config
        public_fxp.bind_session_config(lambda: cfg)
        try:
            session = WorkspaceSession(config=cfg, seed_demo_vars=False)
            res = session.execute(args.eval_expr)
        finally:
            public_fxp.bind_session_config(previous_provider)
        if res.output_text:
            print(res.output_text)
        if res.error_text:
            print(res.error_text, file=sys.stderr)
            return 1
        return 0

    try:
        from .ui.app import launch
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in {
            "PySide6", "qtconsole", "ipykernel", "jupyter_client", "apytypes", "matplotlib"
        }:
            parser.error('GUI dependencies are missing; install with: pip install "rpkbin[calc]"')
        raise

    return launch([sys.argv[0]], mode=args.mode, config_path=args.config)


if __name__ == "__main__":
    raise SystemExit(main())
