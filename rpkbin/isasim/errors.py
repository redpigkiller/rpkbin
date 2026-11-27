"""Public errors and private execution unwinding."""


class IsaSimError(Exception):
    """Base for expected simulator API errors."""


class MCUDefinitionError(IsaSimError):
    pass


class StateAccessError(IsaSimError):
    pass


class LoadError(IsaSimError):
    pass


class AssemblyError(LoadError):
    """Invalid assembly source, with its filename and line number."""


class StimulusError(IsaSimError):
    pass


class DeviceError(IsaSimError):
    pass


class SnapshotError(IsaSimError):
    pass


class TargetError(IsaSimError):
    pass


class DebugError(IsaSimError):
    pass


class UsageError(IsaSimError):
    pass


class _Stop(BaseException):
    def __init__(self, code, detail=None):
        self.code = code
        self.detail = {} if detail is None else detail


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
