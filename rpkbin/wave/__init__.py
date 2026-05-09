"""
wave - Job batch runner with real-time monitoring.

Public API:
    session    - module-level Session singleton
    CmdJob     - alias for WaveCmdJob (PIPE/batch/log-parser friendly)
    FuncJob    - alias for WaveFuncJob
    PtyJob     - alias for PtyCmdJob (PTY/interactive/fake-terminal friendly)
    PtyCmdJob  - full class name for PtyJob
    Hook       - Hook definition
"""

from rpkbin.wave.session import session
from rpkbin.wave.job import WaveCmdJob as CmdJob, WaveFuncJob as FuncJob
from rpkbin.wave.pty_job import PtyCmdJob, PtyCmdJob as PtyJob
from rpkbin.wave.hook import Hook

__all__ = ["session", "CmdJob", "FuncJob", "PtyJob", "PtyCmdJob", "Hook"]
