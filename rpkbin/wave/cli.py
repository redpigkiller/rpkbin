"""
cli.py - Wave command-line entry point.

Usage:
    rpk-wave run <wave_file> [--no-tui] [--workers N]
"""

from __future__ import annotations

import click

from rpkbin.wave.runner import run


@click.group()
def main() -> None:
    """Wave - lightweight job batch runner with real-time monitoring."""
    pass


@main.command("run")
@click.argument("wave_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--no-tui",
    is_flag=True,
    default=False,
    help="CI mode: run headless without opening the TUI.",
)
@click.option(
    "--workers",
    type=click.IntRange(min=1),
    default=None,
    help="Override max_workers set in the wave file.",
)
def cmd_run(wave_file: str, no_tui: bool, workers: int | None) -> None:
    """Load WAVE_FILE and run the job batch."""
    raise SystemExit(run(wave_file, no_tui=no_tui, workers=workers))


if __name__ == "__main__":
    main()

