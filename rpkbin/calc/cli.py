"""CLI entry point for rpk-calc."""

from __future__ import annotations

import argparse
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rpk-calc",
        description="rpk-calc: Engineering & APyTypes Fixed-Point (FXP) interactive calculator.",
    )
    parser.add_argument(
        "-e",
        "--eval",
        dest="eval_expr",
        help="Evaluate a Python/FXP expression headlessly and print the result",
    )
    args = parser.parse_args(argv)

    if args.eval_expr:
        from .core.session import WorkspaceSession

        session = WorkspaceSession(seed_demo_vars=False)
        res = session.execute(args.eval_expr)
        if res.output_text:
            print(res.output_text)
        if res.error_text:
            print(res.error_text)
            return 1
        return 0

    try:
        from .ui.app import launch
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in {"PySide6", "qtconsole", "ipykernel", "jupyter_client", "qtpy"}:
            parser.error('GUI dependencies are missing; install with: pip install -e ".[calc]"')
        raise

    return launch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
