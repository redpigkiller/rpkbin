"""Process capabilities and immutable receiver events."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capabilities:
    has_process: bool = False
    can_send: bool = False
    can_stop: bool = False
    can_interrupt: bool = False
    can_signal: bool = False
    can_kill: bool = False
    can_rerun: bool = False

    def snapshot(self, *, can_rerun=False):
        return dict(asdict(self), can_rerun=can_rerun)


PROCESS_CAPABILITIES = Capabilities(
    has_process=True, can_send=True, can_stop=True, can_interrupt=True,
    can_signal=True, can_kill=True, can_rerun=True,
)


@dataclass(frozen=True)
class Started:
    pass


@dataclass(frozen=True)
class Line:
    text: str
    source: str = "stdout"
    monitor: bool = True


@dataclass(frozen=True)
class SourceError:
    data: dict


@dataclass(frozen=True)
class Finished:
    complete: bool
    reason: str


@dataclass(frozen=True)
class StatusChanged:
    old: str
    new: str


@dataclass(frozen=True)
class ClockTick:
    elapsed: float


SourceEvent = Started | Line | SourceError | Finished | StatusChanged | ClockTick
