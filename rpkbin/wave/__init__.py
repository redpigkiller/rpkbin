"""Wave: Python work definitions, batch runner and local browser controls."""

from rpkbin.job_manager import (
    Check,
    CheckState,
    Event,
    Expect,
    Job,
    JobManager,
    JobStatus,
    Report,
    Result,
    StopPolicy,
    Track,
)
from .cli import build_demo_manager, load_work_file, main, run_headless

__all__ = [
    "Check",
    "CheckState",
    "Report",
    "Track",
    "Event",
    "Expect",
    "HTML_PAGE",
    "Job",
    "JobManager",
    "JobStatus",
    "Result",
    "StopPolicy",
    "WaveServer",
    "build_demo_manager",
    "load_work_file",
    "launch",
    "main",
    "run_headless",
]


def __getattr__(name):
    # Headless use and work-file imports do not initialize the browser server.
    if name in ("HTML_PAGE", "WaveServer", "launch"):
        from . import web

        return getattr(web, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
