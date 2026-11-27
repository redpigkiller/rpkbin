"""Wave execution facts and non-process control."""

from dataclasses import dataclass
from typing import TypedDict

from rpkbin.jobcore import Capabilities, ControlReceipt
from rpkbin.jobcore.events import PROCESS_CAPABILITIES


def source_capabilities(kind):
    return PROCESS_CAPABILITIES if kind == "process" else NoControl.capabilities


def source_origin(kind):
    if kind == "process":
        return "managed", None
    return "external", {
        "tail": "External (tail)",
        "file": "External (file)",
        "reanalysis": "External (replay)",
    }.get(kind)


class ExecutionFacts(TypedDict, total=False):
    status: str
    returncode: int | None
    elapsed: float
    finished: bool
    stop_requested: bool
    timed_out: bool
    output_complete: bool
    job_id: str
    attempt_id: str
    attempt_number: int


@dataclass
class Run:
    """The captured execution facts and control channel of one analysis."""

    facts: ExecutionFacts
    control: object

    def execution(self) -> ExecutionFacts:
        return self.facts.copy()


class NoControl:
    """Reject source controls through the regular receipt and audit path."""

    capabilities = Capabilities()

    def __getattr__(self, action):
        if action in ("send", "stop", "interrupt", "send_ctrl_c", "send_signal", "kill"):
            return self._reject
        raise AttributeError(action)

    @staticmethod
    def _reject(*args, **kwargs):
        return ControlReceipt("rejected", "no control channel")
