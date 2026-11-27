"""Wave: Python work definitions, batch runner and local browser controls."""

from .archive import ArchivePolicy, RunArchive, prune_archives
from .sources import FileSource, ReanalysisSource, SourceResult, TailSource

from .cli import build_demo_workspace, load_work_file, main, run_headless
from .workspace import WaveWorkspace

__all__ = [
    "ArchivePolicy", "WaveWorkspace", "RunArchive", "prune_archives",
    "FileSource", "TailSource", "ReanalysisSource",
    "SourceResult", "WaveServer", "build_demo_workspace", "load_work_file",
    "launch", "main", "run_headless",
]


def __getattr__(name):
    # Headless use and work-file imports do not initialize the browser server.
    if name in ("WaveServer", "launch"):
        from . import web

        return getattr(web, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
