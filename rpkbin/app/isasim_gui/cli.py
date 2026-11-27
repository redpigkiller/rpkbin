"""Command-line interface for the isasim web debugger."""

from __future__ import annotations

import argparse
import re
import sys
import webbrowser

from rpkbin import __version__
from rpkbin.isasim import IsaSimError

from .session import DebugSession
from .server import create_server


def _mnemonics(value: str) -> tuple[str, ...]:
    return tuple(word for word in re.split(r"[,\s]+", value.strip()) if word)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpk-isasim-gui",
        description="Local GUI debugger for rpkbin.isasim",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("mcu", help="Path to MCU definition Python script (.py)")
    parser.add_argument("program", help="Path to assembly source (.asm) or bundle (.isa.json)")
    parser.add_argument(
        "--script",
        default=None,
        help="Optional Python setup script defining setup(sim)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost"),
        help="Local GUI bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8000,
        help="Port number to bind the debugger server (default: 8000)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the GUI automatically in a web browser",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically (default)",
    )
    parser.add_argument(
        "--max-history",
        type=int,
        default=200,
        help="Maximum back-step snapshots (default: 200, 0 disables history)",
    )
    parser.add_argument("--call-mnemonics", type=_mnemonics, default=(),
                        help="Comma-separated call mnemonics for models without instruction kinds")
    parser.add_argument("--return-mnemonics", type=_mnemonics, default=(),
                        help="Comma-separated return mnemonics for models without instruction kinds")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.open and args.no_browser:
        parser.error("--open and --no-browser cannot be combined")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.max_history < 0:
        parser.error("--max-history must be non-negative")
    if {word.casefold() for word in args.call_mnemonics} & {word.casefold() for word in args.return_mnemonics}:
        parser.error("Call and return mnemonic lists must be disjoint")

    bind_host = "127.0.0.1" if args.host == "localhost" else args.host
    try:
        session = DebugSession(
            mcu_path=args.mcu,
            asm_path=args.program,
            script_path=args.script,
            max_history=args.max_history,
            call_mnemonics=args.call_mnemonics,
            return_mnemonics=args.return_mnemonics,
        )
        server = create_server(session, host=bind_host, port=args.port)
    except (IsaSimError, OSError, SyntaxError, ImportError, ValueError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    url = f"http://{args.host}:{server.server_port}"
    print(f"Starting isasim GUI at {url}")
    if args.open and not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping isasim GUI")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
