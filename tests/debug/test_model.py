from dataclasses import FrozenInstanceError

import pytest

from rpkbin.debug import (
    AddressSpace,
    DebugCapabilities,
    DebugLocation,
    ExecutionContext,
    InstructionBreakpoint,
    SourceBreakpoint,
    StopReason,
    StopResult,
)


def test_records_are_immutable_and_addresses_are_unit_neutral():
    space = AddressSpace("program", unit_bits=16)
    with pytest.raises(FrozenInstanceError):
        space.unit_bits = 8  # type: ignore[misc]
    assert InstructionBreakpoint("program", 7).address == 7


@pytest.mark.parametrize(
    "factory,args",
    [
        (AddressSpace, ("", 8)),
        (AddressSpace, ("program", 0)),
        (ExecutionContext, (0, "main")),
        (DebugLocation, ("program", -1)),
        (SourceBreakpoint, ("main.asm", 0)),
    ],
)
def test_invalid_records_fail_closed(factory, args):
    with pytest.raises(ValueError):
        factory(*args)


def test_stop_can_report_fault_without_location():
    result = StopResult(
        StopReason.EXCEPTION,
        context_id=1,
        location=None,
        description="decode fault",
    )
    assert result.location is None
    assert DebugCapabilities().supports_reverse_step is False
