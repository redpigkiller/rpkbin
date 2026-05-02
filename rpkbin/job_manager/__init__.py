"""job_manager sub-package — minimal, robust job scheduling."""

from rpkbin.job_manager.job import (
    Job,
    JobStatus,
    PENDING, RUNNING, DONE, FAILED, CANCELLED,
)
from rpkbin.job_manager.cmd_job import CmdJob
from rpkbin.job_manager.func_job import FuncJob
from rpkbin.job_manager.manager import JobManager

__all__ = [
    "JobManager", "Job", "CmdJob", "FuncJob",
    "JobStatus", "PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED",
]
