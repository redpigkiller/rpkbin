"""
wave - Job batch runner with real-time monitoring.

Public API:
    session   - module-level Session singleton
    CmdJob    - alias for WaveCmdJob
    FuncJob   - alias for WaveFuncJob
    Hook      - Hook definition
"""

from rpkbin.wave.session import session
from rpkbin.wave.job import WaveCmdJob as CmdJob, WaveFuncJob as FuncJob
from rpkbin.wave.hook import Hook

__all__ = ["session", "CmdJob", "FuncJob", "Hook"]
